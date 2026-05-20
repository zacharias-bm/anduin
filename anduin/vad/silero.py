from __future__ import annotations
from pathlib import Path

import torch
import torchaudio

_model = None
_utils = None


def _load():
    global _model, _utils
    if _model is None:
        _model, _utils = torch.hub.load(
            repo_or_dir="snakers4/silero-vad",
            model="silero_vad",
            force_reload=False,
            trust_repo=True,
        )


def get_speech_segments(audio_path: Path, threshold: float = 0.5) -> list[dict]:
    """Returns [{start, end}] in seconds for all speech regions."""
    _load()
    get_speech_ts = _utils[0]

    wav, sr = torchaudio.load(str(audio_path))
    if sr != 16000:
        wav = torchaudio.functional.resample(wav, sr, 16000)
    wav = wav.squeeze(0)

    timestamps = get_speech_ts(wav, _model, threshold=threshold, return_seconds=True)
    return [{"start": t["start"], "end": t["end"]} for t in timestamps]
