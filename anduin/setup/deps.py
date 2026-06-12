from __future__ import annotations

import subprocess
import sys
from typing import Callable

# (import_name, pip_name) — import_name is what we try to import to check presence
PACKAGES: list[tuple[str, str]] = [
    ("psutil",           "psutil"),
    ("requests",         "requests"),
    ("sounddevice",      "sounddevice"),
    ("soundfile",        "soundfile"),
    ("keyring",          "keyring"),
    ("rumps",            "rumps"),
    ("huggingface_hub",  "huggingface-hub"),
    ("imageio_ffmpeg",   "imageio-ffmpeg"),
    ("torch",            "torch"),
    ("torchaudio",       "torchaudio"),
    ("mlx_whisper",      "mlx-whisper"),
    ("pyannote.audio",   "pyannote.audio"),
    ("yaml",             "pyyaml"),
]


def missing() -> list[str]:
    """Return pip names for any packages that cannot be imported."""
    result = []
    for import_name, pip_name in PACKAGES:
        try:
            __import__(import_name)
        except ImportError:
            result.append(pip_name)
    return result


def install(
    packages: list[str],
    line_callback: Callable[[str], None] | None = None,
) -> bool:
    """Install packages via pip. Returns True on success."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "pip", "install", "--quiet"] + packages,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    for line in proc.stdout:
        if line_callback:
            line_callback(line.rstrip())
    proc.wait()
    return proc.returncode == 0
