from __future__ import annotations
import json
from pathlib import Path


def align(
    diarization: list[dict],
    transcript: list[dict],
    speaker_names: dict[str, str] | None = None,
) -> list[dict]:
    """Merges diarization and transcript into [{speaker, start, end, text}]."""
    speaker_names = speaker_names or {}
    segments = []
    for seg in transcript:
        if diarization:
            raw_speaker = _dominant_speaker(seg["start"], seg["end"], diarization)
            speaker = speaker_names.get(raw_speaker, raw_speaker)
        else:
            speaker = "Speaker"
        segments.append({
            "speaker": speaker,
            "start": seg["start"],
            "end": seg["end"],
            "text": seg["text"],
        })
    return _merge_consecutive(segments)


def write_transcript(segments: list[dict], output_dir: Path) -> tuple[Path, Path]:
    json_path = output_dir / "transcript.json"
    md_path = output_dir / "transcript.md"

    json_path.write_text(json.dumps(segments, indent=2, ensure_ascii=False))

    lines = []
    for seg in segments:
        lines.append(f"**{seg['speaker']}** `{_fmt_time(seg['start'])}`\n{seg['text']}\n")
    md_path.write_text("\n".join(lines))

    return json_path, md_path


def _dominant_speaker(start: float, end: float, diarization: list[dict]) -> str:
    overlap: dict[str, float] = {}
    for d in diarization:
        o = min(end, d["end"]) - max(start, d["start"])
        if o > 0:
            overlap[d["speaker"]] = overlap.get(d["speaker"], 0.0) + o
    return max(overlap, key=overlap.get) if overlap else "Unknown"


def _merge_consecutive(segments: list[dict]) -> list[dict]:
    if not segments:
        return []
    merged = [segments[0].copy()]
    for seg in segments[1:]:
        if seg["speaker"] == merged[-1]["speaker"]:
            merged[-1]["end"] = seg["end"]
            merged[-1]["text"] += " " + seg["text"]
        else:
            merged.append(seg.copy())
    return merged


def _fmt_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
