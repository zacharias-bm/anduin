from __future__ import annotations

from pathlib import Path
from typing import Callable

import keyring
from huggingface_hub import hf_hub_download, list_repo_files, try_to_load_from_cache

KEYCHAIN_SERVICE = "Anduin"
KEYCHAIN_KEY = "huggingface_token"

WHISPER_REPOS = {
    "mlx-community/whisper-medium-mlx-4bit":  "mlx-community/whisper-medium-mlx-4bit",
    "mlx-community/whisper-medium-mlx":       "mlx-community/whisper-medium-mlx",
    "mlx-community/whisper-large-v3-turbo":   "mlx-community/whisper-large-v3-turbo",
}
PYANNOTE_REPO = "pyannote/speaker-diarization-3.1"

WHISPER_SIZES = {
    "mlx-community/whisper-medium-mlx-4bit":  "0.4 GB",
    "mlx-community/whisper-medium-mlx":       "1.5 GB",
    "mlx-community/whisper-large-v3-turbo":   "3.1 GB",
}

# Progress callback type: (current_file, total_files, filename)
ProgressFn = Callable[[int, int, str], None]


def whisper_is_downloaded(model_size: str) -> bool:
    repo = WHISPER_REPOS.get(model_size, model_size)
    cached = try_to_load_from_cache(repo, "config.json")
    if cached is not None:
        return True
    return try_to_load_from_cache(repo, "weights.npz") is not None


def download_whisper(model_size: str, progress: ProgressFn | None = None) -> Path:
    repo = WHISPER_REPOS.get(model_size, model_size)
    files = list(list_repo_files(repo))
    total = len(files)
    last_path = None
    for i, filename in enumerate(files):
        if progress:
            progress(i + 1, total, filename)
        last_path = hf_hub_download(repo_id=repo, filename=filename)
    return Path(last_path).parent if last_path else Path()


# Byte-level progress callback: (bytes_downloaded, total_bytes, filename)
ByteProgressFn = Callable[[int, int, str], None]


def download_whisper_with_progress(model_size: str, progress: ByteProgressFn | None = None) -> Path:
    """Download Whisper model with byte-level progress via custom tqdm class."""
    repo = WHISPER_REPOS.get(model_size, model_size)
    files = list(list_repo_files(repo))

    # Track cumulative bytes across all files
    state = {"downloaded": 0, "total": 0, "current_file": ""}

    class ProgressTqdm:
        """Minimal tqdm-compatible class that forwards to our callback."""
        def __init__(self, *args, total=None, **kwargs):
            self._total = total or 0
            # Add this file's size to global total on first encounter
            if self._total > 0:
                state["total"] += self._total

        def update(self, n=1):
            state["downloaded"] += n
            if progress:
                progress(state["downloaded"], state["total"], state["current_file"])

        def close(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    last_path = None
    for filename in files:
        state["current_file"] = filename
        if progress:
            progress(state["downloaded"], max(state["total"], 1), filename)
        last_path = hf_hub_download(repo_id=repo, filename=filename,
                                    tqdm_class=ProgressTqdm)

    return Path(last_path).parent if last_path else Path()


def pyannote_is_downloaded() -> bool:
    return try_to_load_from_cache(PYANNOTE_REPO, "config.yaml") is not None


def download_pyannote(token: str, progress: ProgressFn | None = None):
    keyring.set_password(KEYCHAIN_SERVICE, KEYCHAIN_KEY, token)

    # Download the main config repo file-by-file so we can show progress
    files = list(list_repo_files(PYANNOTE_REPO, token=token))
    total = len(files)
    for i, filename in enumerate(files):
        if progress:
            progress(i + 1, total, filename)
        hf_hub_download(repo_id=PYANNOTE_REPO, filename=filename, token=token)

    # Initialize the full pipeline — downloads any referenced sub-models silently
    if progress:
        progress(total, total, "Initializing pipeline…")
    from pyannote.audio import Pipeline
    Pipeline.from_pretrained(PYANNOTE_REPO, token=token)


def get_hf_token() -> str | None:
    return keyring.get_password(KEYCHAIN_SERVICE, KEYCHAIN_KEY)
