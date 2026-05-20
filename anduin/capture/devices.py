from __future__ import annotations
"""Device detection and per-mode preference helpers."""
import sounddevice as sd

INPERSON_KEY = "device_inperson"
DIGITAL_KEY = "device_digital"


def list_input_devices() -> list[dict]:
    return [
        {"index": i, "name": d["name"]}
        for i, d in enumerate(sd.query_devices())
        if d["max_input_channels"] > 0
    ]


def find_digital_device() -> dict | None:
    """Find an Aggregate Device or raw BlackHole input for digital meetings.

    An Aggregate Device (configured in Audio MIDI Setup to combine mic + BlackHole)
    is preferred over a raw BlackHole device because it captures both streams.
    """
    aggregate, blackhole = None, None
    for d in list_input_devices():
        name = d["name"].lower()
        if "aggregate" in name and aggregate is None:
            aggregate = d
        elif "blackhole" in name and blackhole is None:
            blackhole = d
    return aggregate or blackhole


def system_default_input() -> dict | None:
    try:
        idx = sd.default.device[0]
        if idx < 0:
            return None
        return {"index": idx, "name": sd.query_devices(idx)["name"]}
    except Exception:
        return None


def device_for_mode(mode: str) -> int | None:
    """Return the stored device index for 'inperson' or 'digital', falling back to auto-detect."""
    from anduin.storage.store import get_config

    stored = get_config(INPERSON_KEY if mode == "inperson" else DIGITAL_KEY)
    if stored is not None:
        return stored

    # Auto-detect fallback
    if mode == "inperson":
        d = system_default_input()
    else:
        d = find_digital_device()
    return d["index"] if d else None
