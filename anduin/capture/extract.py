from __future__ import annotations
import subprocess
from pathlib import Path

import imageio_ffmpeg

AUDIO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}


def needs_extraction(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_EXTENSIONS


def extract_audio(video_path: Path, output_path: Path) -> Path:
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run(
        [ffmpeg, "-i", str(video_path), "-ac", "1", "-ar", "16000", "-y", str(output_path)],
        check=True,
        capture_output=True,
    )
    return output_path
