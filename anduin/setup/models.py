from __future__ import annotations

from pathlib import Path
from typing import Callable

import keyring
from huggingface_hub import hf_hub_download, list_repo_files, try_to_load_from_cache

KEYCHAIN_SERVICE = "Anduin"
KEYCHAIN_KEY = "huggingface_token"

WHISPER_REPOS = {
    "medium":   "Systran/faster-whisper-medium",
    "large-v2": "Systran/faster-whisper-large-v2",
    "large-v3": "Systran/faster-whisper-large-v3",
}
PYANNOTE_REPO = "pyannote/speaker-diarization-3.1"

WHISPER_SIZES = {
    "medium":   "1.5 GB",
    "large-v2": "3.1 GB",
    "large-v3": "3.1 GB",
}

# Progress callback type: (current_file, total_files, filename)
ProgressFn = Callable[[int, int, str], None]


def whisper_is_downloaded(model_size: str) -> bool:
    repo = WHISPER_REPOS.get(model_size)
    return repo is not None and try_to_load_from_cache(repo, "config.json") is not None


def download_whisper(model_size: str, progress: ProgressFn | None = None) -> Path:
    repo = WHISPER_REPOS[model_size]
    files = list(list_repo_files(repo))
    total = len(files)
    last_path = None
    for i, filename in enumerate(files):
        if progress:
            progress(i + 1, total, filename)
        last_path = hf_hub_download(repo_id=repo, filename=filename)
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
