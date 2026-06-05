from __future__ import annotations
import shutil
from pathlib import Path
from typing import Callable

from anduin.capture.extract import extract_audio, needs_extraction
from anduin.diarization.diarizer import diarize
from anduin.hardware.detect import detect as detect_hardware
from anduin.merge.aligner import align, write_transcript
from anduin.storage.store import get_config, get_speaker_names, meeting_dir, save_summary
from anduin.summarization.engine import summarize
from anduin.transcription.whisper import transcribe

ProgressCallback = Callable[[str, str], None]  # (stage, message)


def run(
    audio_path: Path,
    title: str,
    whisper_model: str | None = None,
    llm_model: str | None = None,
    auto_summarize: bool = True,
    progress: ProgressCallback | None = None,
) -> Path:
    """
    Run the full pipeline on an audio or video file.
    Returns the meeting output directory.
    """
    def _p(stage: str, msg: str):
        if progress:
            progress(stage, msg)

    hw = detect_hardware()
    whisper_model = whisper_model or hw["whisper_model"]
    llm_model = llm_model or hw["llm_model"]

    out_dir = meeting_dir(title)

    if needs_extraction(audio_path):
        _p("extract", f"Extracting audio from {audio_path.name}...")
        audio_path = extract_audio(audio_path, out_dir / "audio.wav")
    else:
        dest = out_dir / "audio.wav"
        if audio_path.resolve() != dest.resolve():
            shutil.copy2(audio_path, dest)
        audio_path = dest

    diarization_enabled = get_config("diarization_enabled", False)

    if diarization_enabled:
        _p("diarize", "Identifying speakers...")
        diarization = diarize(audio_path)
        print(f"[pipeline] diarize: found {len(diarization)} segments", flush=True)
    else:
        diarization = []
        print("[pipeline] diarize: skipped (disabled)", flush=True)

    _p("transcribe", "Transcribing...")
    dictionary = get_config("dictionary", [])
    transcript = transcribe(audio_path, model_size=whisper_model, dictionary=dictionary or None)
    print(f"[pipeline] transcribe: found {len(transcript)} segments", flush=True)

    _p("align", "Aligning transcript with speakers...")
    segments = align(diarization, transcript, speaker_names=get_speaker_names())
    print(f"[pipeline] align: produced {len(segments)} merged segments", flush=True)
    write_transcript(segments, out_dir)

    if auto_summarize:
        _p("summarize", "Generating summary...")
        summary = summarize(
            segments,
            model=llm_model,
            progress=None,
        )
        save_summary(out_dir, summary, title=title)
    else:
        _p("skip_summarize", "Skipping auto-summarization")
        # Still index it so it shows up in the list
        from anduin.storage.store import _index_meeting
        _index_meeting(out_dir, title=title)

    # Delete audio file after processing unless the user opted to keep it
    if not get_config("keep_audio", False):
        audio_file = out_dir / "audio.wav"
        if audio_file.exists():
            audio_file.unlink()
            print("[pipeline] audio file removed (keep_audio=off)", flush=True)

    _p("done", str(out_dir))
    return out_dir


def summarize_meeting(
    meeting_path: Path,
    template_id: str = "standard",
    custom_prompt: str | None = None,
    llm_model: str | None = None,
    progress: ProgressCallback | None = None,
) -> str:
    """Run summarization on an already-processed meeting."""
    import json as _json
    transcript_path = meeting_path / "transcript.json"
    if not transcript_path.exists():
        raise FileNotFoundError(f"No transcript found at {transcript_path}")
    segments = _json.loads(transcript_path.read_text())

    hw = detect_hardware()
    llm_model = llm_model or hw["llm_model"]

    def _p(stage: str, msg: str):
        if progress:
            progress(stage, msg)

    _p("summarize", "Generating summary...")
    summary = summarize(
        segments,
        model=llm_model,
        template_id=template_id,
        custom_prompt=custom_prompt,
        progress=None,
    )
    # Get existing title from DB if possible
    from anduin.storage.store import _connect
    with _connect() as con:
        row = con.execute("SELECT title FROM meetings WHERE path = ?", (str(meeting_path),)).fetchone()
        existing_title = row[0] if row else None
    
    save_summary(meeting_path, summary, title=existing_title)
    _p("done", "Summary complete")
    return summary
