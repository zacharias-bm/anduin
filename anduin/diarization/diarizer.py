from __future__ import annotations
from pathlib import Path

import keyring
import numpy as np
import soundfile as sf
import torch
from pyannote.audio import Pipeline

KEYCHAIN_SERVICE = "Anduin"
KEYCHAIN_KEY = "huggingface_token"

_pipeline = None


def _load():
    global _pipeline
    if _pipeline is None:
        token = keyring.get_password(KEYCHAIN_SERVICE, KEYCHAIN_KEY)
        if not token:
            raise RuntimeError("HuggingFace token not found in Keychain. Run setup wizard first.")
        _pipeline = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1",
            token=token,
        )


def _load_waveform(audio_path: Path) -> dict:
    """Load audio via soundfile, bypassing torchcodec which isn't available."""
    samples, sample_rate = sf.read(str(audio_path), dtype="float32", always_2d=True)
    # soundfile returns (samples, channels); pyannote expects (channels, samples)
    waveform = torch.tensor(samples.T)
    return {"waveform": waveform, "sample_rate": sample_rate}


def unload():
    global _pipeline
    _pipeline = None
    import gc
    gc.collect()


def diarize(audio_path: Path) -> list[dict]:
    """Returns [{speaker, start, end}] sorted by start time."""
    _load()
    result = _pipeline(_load_waveform(audio_path))
    # pyannote >= 3.3 wraps output in DiarizeOutput; older versions return Annotation directly
    annotation = result.speaker_diarization if hasattr(result, "speaker_diarization") else result
    segments = [
        {
            "speaker": speaker,
            "start": round(turn.start, 3),
            "end": round(turn.end, 3),
        }
        for turn, _, speaker in annotation.itertracks(yield_label=True)
    ]
    return sorted(segments, key=lambda s: s["start"])
