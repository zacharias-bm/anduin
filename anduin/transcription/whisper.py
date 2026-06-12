from __future__ import annotations
from pathlib import Path

import numpy as np
import soundfile as sf

import mlx_whisper

WHISPER_SAMPLE_RATE = 16000


def unload():
    import gc
    gc.collect()


def _load_audio(audio_path: Path) -> np.ndarray:
    """Load audio as float32 numpy array at 16kHz mono.

    Uses soundfile so we don't need ffmpeg on PATH.
    """
    audio, sr = sf.read(str(audio_path), dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sr != WHISPER_SAMPLE_RATE:
        import scipy.signal
        num_samples = int(len(audio) * WHISPER_SAMPLE_RATE / sr)
        audio = scipy.signal.resample(audio, num_samples).astype(np.float32)
    peak = np.abs(audio).max() if len(audio) > 0 else 0.0
    rms = np.sqrt(np.mean(audio ** 2)) if len(audio) > 0 else 0.0
    print(
        f"[whisper] loaded {len(audio)/WHISPER_SAMPLE_RATE:.1f}s audio, "
        f"sr={sr}, peak={peak:.4f}, rms={rms:.4f}",
        flush=True,
    )
    return audio


def transcribe(
    audio_path: Path,
    model_size: str = "mlx-community/whisper-large-v3-turbo",
    dictionary: list[str] | None = None,
    context: str | None = None,
) -> list[dict]:
    """Returns [{start, end, text, words}] with word-level timestamps.

    Args:
        model_size: HuggingFace repo for the mlx-whisper model.
        context: Trailing text from the previous chunk, fed as initial_prompt
            for cross-boundary continuity.
    """
    parts = []
    if dictionary:
        parts.append(", ".join(dictionary))
    if context:
        parts.append(context)
    initial_prompt = ". ".join(parts) if parts else None

    audio = _load_audio(audio_path)

    result = mlx_whisper.transcribe(
        audio,
        path_or_hf_repo=model_size,
        language=None,
        word_timestamps=True,
        initial_prompt=initial_prompt,
        condition_on_previous_text=False,
        hallucination_silence_threshold=2.0,
        compression_ratio_threshold=1.8,
    )

    segments = _extract_segments(result)

    if not segments and len(audio) / WHISPER_SAMPLE_RATE > 1.0:
        duration = len(audio) / WHISPER_SAMPLE_RATE
        print(
            f"[whisper] 0 segments for {duration:.0f}s audio, "
            "retrying with lower no_speech_threshold",
            flush=True,
        )
        result = mlx_whisper.transcribe(
            audio,
            path_or_hf_repo=model_size,
            language=None,
            word_timestamps=True,
            initial_prompt=initial_prompt,
            condition_on_previous_text=False,
            no_speech_threshold=0.3,
            hallucination_silence_threshold=2.0,
        )
        segments = _extract_segments(result)

    segments = _remove_hallucinations(segments)
    return segments


def _extract_segments(result: dict) -> list[dict]:
    raw_segments = result.get("segments", [])
    out = []
    for seg in raw_segments:
        text = seg.get("text", "").strip()
        if not text:
            continue
        words = []
        for w in seg.get("words") or []:
            words.append({
                "word": w.get("word", ""),
                "start": round(w.get("start", 0.0), 3),
                "end": round(w.get("end", 0.0), 3),
                "probability": round(w.get("probability", 0.0), 3),
            })
        out.append({
            "start": round(seg.get("start", 0.0), 3),
            "end": round(seg.get("end", 0.0), 3),
            "text": text,
            "words": words,
        })
    return out


def _remove_hallucinations(segments: list[dict]) -> list[dict]:
    """Drop segments that are repeated hallucinations.

    Whisper sometimes gets stuck in a loop producing the same phrase
    over and over ("Thanks for watching!", "you", etc.).
    """
    if len(segments) < 3:
        return segments

    out = []
    for seg in segments:
        text = seg["text"].strip().lower()
        # Count how many of the last few accepted segments have the same text
        recent = [s["text"].strip().lower() for s in out[-3:]]
        if recent.count(text) >= 2:
            print(f"[whisper] dropped hallucinated segment: {seg['text']!r}", flush=True)
            continue
        out.append(seg)
    return out
