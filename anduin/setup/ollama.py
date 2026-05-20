from __future__ import annotations
import json
import os
import shutil
import subprocess
import time
from typing import Callable

import requests

OLLAMA_API = "http://localhost:11434"

# Tracks whether *we* started Ollama this session, so we know whether to stop
# it on quit. If Ollama was already running when Anduin launched, we leave it
# alone — the user is managing it themselves.
_we_started_ollama = False


def is_installed() -> bool:
    return shutil.which("ollama") is not None


def is_running() -> bool:
    try:
        requests.get(f"{OLLAMA_API}/api/tags", timeout=2)
        return True
    except Exception:
        return False


def ensure_running():
    """Start Ollama in the background if not already running.

    Runs at full priority with Metal enabled — the goal is to make model
    loading as fast as possible so any unavoidable memory-pressure spike
    is short (1-3 s) rather than drawn out.  The model is loaded on-demand
    per summarization and unloaded immediately after (keep_alive=0).
    """
    global _we_started_ollama
    if is_running():
        return
    env = {**os.environ, "OLLAMA_MAX_LOADED_MODELS": "1"}
    subprocess.Popen(
        ["ollama", "serve"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    for _ in range(30):
        time.sleep(0.5)
        if is_running():
            _we_started_ollama = True
            return
    raise RuntimeError("Ollama failed to start within 15 seconds.")


def unload_model(model: str):
    """Unload a model from Ollama to free memory."""
    if not is_running():
        return
    try:
        requests.post(
            f"{OLLAMA_API}/api/generate",
            json={"model": model, "prompt": "", "keep_alive": 0},
            timeout=3,
        )
    except Exception:
        pass


def stop_server():
    """Stop the Ollama server, but only if we started it this session.

    Uses SIGTERM for a graceful shutdown, with a short timeout so we
    never block the app's quit sequence.
    """
    if not _we_started_ollama:
        return
    try:
        import signal
        # Find ollama PIDs ourselves so we can send SIGTERM (not SIGKILL)
        result = subprocess.run(
            ["pgrep", "-f", "ollama serve"],
            capture_output=True, text=True, timeout=2,
        )
        for pid_str in result.stdout.strip().split("\n"):
            pid_str = pid_str.strip()
            if pid_str:
                try:
                    os.kill(int(pid_str), signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    pass
    except Exception:
        pass


def pull_model(model: str, progress: Callable[[int, int], None] | None = None):
    response = requests.post(
        f"{OLLAMA_API}/api/pull",
        json={"name": model, "stream": True},
        stream=True,
        timeout=3600,
    )
    response.raise_for_status()
    for line in response.iter_lines():
        if line:
            data = json.loads(line)
            if progress and "completed" in data and "total" in data:
                progress(data["completed"], data["total"])
            if data.get("status") == "success":
                break


def model_is_pulled(model: str) -> bool:
    try:
        resp = requests.get(f"{OLLAMA_API}/api/tags", timeout=5)
        names = [m["name"] for m in resp.json().get("models", [])]
        return any(model in n for n in names)
    except Exception:
        return False
