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
    if ram_gb <= 8:
        return "mlx-community/whisper-medium-mlx-4bit"
    if ram_gb <= 16:
        return "mlx-community/whisper-medium-mlx"
    return "mlx-community/whisper-large-v3-turbo"


def _llm_model(chip: str, ram_gb: int) -> str:
    if ram_gb <= 8:
        return "qwen2.5:3b"
    if ram_gb <= 16:
        return "qwen2.5:7b"
    return "qwen2.5:14b"
