from __future__ import annotations
import subprocess
import psutil


def detect() -> dict:
    ram_gb = psutil.virtual_memory().total // (1024 ** 3)
    chip = _chip_generation()
    return {
        "chip": chip,
        "ram_gb": ram_gb,
        "whisper_model": _whisper_model(chip, ram_gb),
        "llm_model": _llm_model(chip, ram_gb),
    }


def _chip_generation() -> str:
    try:
        result = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True, text=True, check=True,
        )
        brand = result.stdout.strip()
        for gen in ("M4", "M3", "M2", "M1"):
            if gen in brand:
                return gen
    except Exception:
        pass
    return "unknown"


def _whisper_model(chip: str, ram_gb: int) -> str:
    if chip == "M1" and ram_gb <= 8:
        return "medium"
    if chip in ("M2", "M3", "M4") and ram_gb >= 16:
        return "large-v3"
    return "large-v2"


def _llm_model(chip: str, ram_gb: int) -> str:
    if chip == "M1" and ram_gb <= 8:
        return "gemma4:e2b"
    if ram_gb >= 32:
        return "gemma4:26b"
    return "gemma4:e4b"
