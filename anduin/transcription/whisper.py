from __future__ import annotations
from pathlib import Path

from faster_whisper import WhisperModel

_model: WhisperModel | None = None
_loaded_size: str | None = None


def _load(model_size: str):
    global _model, _loaded_size
    if _model is None or _loaded_size != model_size:
        _model = WhisperModel(model_size, device="cpu", compute_type="int8")
        _loaded_size = model_size


def unload():
    global _model, _loaded_size
    _model = None
    _loaded_size = None
    import gc
    gc.collect()


def transcribe(audio_path: Path, model_size: str = "large-v3") -> list[dict]:
    """Returns [{start, end, text, words}] with word-level timestamps."""
    _load(model_size)
    segments, _ = _model.transcribe(
        str(audio_path),
        language=None,       # auto-detect Swedish / English
        word_timestamps=True,
        vad_filter=True,     # use built-in Silero VAD for robustness
    )
    return [
        {
            "start": round(seg.start, 3),
            "end": round(seg.end, 3),
            "text": seg.text.strip(),
            "words": [
                {
                    "word": w.word,
                    "start": round(w.start, 3),
                    "end": round(w.end, 3),
                    "probability": round(w.probability, 3),
                }
                for w in (seg.words or [])
            ],
        }
        for seg in segments
    ]
