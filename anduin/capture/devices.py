from __future__ import annotations
"""Device detection and per-mode preference helpers."""
import sounddevice as sd

INPERSON_KEY = "device_inperson"


def list_input_devices() -> list[dict]:
    return [
        {"index": i, "name": d["name"]}
        for i, d in enumerate(sd.query_devices())
        if d["max_input_channels"] > 0
    ]


def system_default_input() -> dict | None:
    try:
        idx = sd.default.device[0]
        if idx < 0:
            return None
        return {"index": idx, "name": sd.query_devices(idx)["name"]}
    except Exception:
        return None


def device_for_mode(mode: str) -> int | None:
    """Return the stored device index for in-person recording.

    Digital mode uses ScreenCaptureKit (system audio + mic) and
    does not need a device index.
    """
    from anduin.storage.store import get_config

    if mode == "digital":
        return None  # Handled by SystemAudioRecorder

    stored = get_config(INPERSON_KEY)
    if stored is not None:
        # Validate the stored device is a real mic, not a virtual device
        try:
            import sounddevice as sd
            dev = sd.query_devices(stored)
            name = dev["name"].lower()
            if dev["max_input_channels"] > 0 and "blackhole" not in name:
                return stored
        except Exception:
            pass

    d = system_default_input()
    return d["index"] if d else None
