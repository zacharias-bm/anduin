#!/usr/bin/env python3
"""
Anduin bootstrap launcher.

Uses only the Python standard library so it runs on a bare install.
Checks Python version, installs missing packages, then starts the app.
Run with:  python3 launch.py
"""
import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

MIN_PYTHON = (3, 9)
PROJECT_DIR = Path(__file__).parent


def _version_ok() -> bool:
    return sys.version_info >= MIN_PYTHON


def _show_version_error():
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror(
        "Python Version",
        f"Anduin requires Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer.\n"
        f"You have Python {sys.version.split()[0]}.\n\n"
        "Install a newer Python from python.org or via Homebrew:\n"
        "  brew install python@3.12",
    )
    root.destroy()


def _missing_packages() -> list[str]:
    """Check for missing packages without importing the app (avoids import errors)."""
    checks = [
        ("psutil",          "psutil"),
        ("requests",        "requests"),
        ("sounddevice",     "sounddevice"),
        ("soundfile",       "soundfile"),
        ("keyring",         "keyring"),
        ("rumps",           "rumps"),
        ("huggingface_hub", "huggingface-hub"),
        ("imageio_ffmpeg",  "imageio-ffmpeg"),
        ("torch",           "torch"),
        ("torchaudio",      "torchaudio"),
        ("mlx_whisper",     "mlx-whisper"),
        ("pyannote.audio",  "pyannote.audio"),
        ("yaml",            "pyyaml"),
    ]
    missing = []
    for import_name, pip_name in checks:
        try:
            __import__(import_name)
        except ImportError:
            missing.append(pip_name)
    return missing


def _install_with_progress(packages: list[str]):
    """Show a tkinter window with live pip output, then restart the process."""
    root = tk.Tk()
    root.title("Anduin — Installing Dependencies")
    root.resizable(False, False)
    root.configure(bg="white")
    w, h = 540, 340
    root.geometry(f"{w}x{h}+{(root.winfo_screenwidth()-w)//2}+{(root.winfo_screenheight()-h)//2}")

    # Header
    hdr = tk.Frame(root, bg="#1a1a2e", height=52)
    hdr.pack(fill="x")
    hdr.pack_propagate(False)
    tk.Label(hdr, text="Installing Dependencies", font=("SF Pro Display", 15, "bold"),
             fg="white", bg="#1a1a2e").pack(side="left", padx=20)

    tk.Label(root, text=f"Installing {len(packages)} package(s)…",
             font=("SF Pro Text", 12), bg="white", fg="#333",
             anchor="w").pack(fill="x", padx=20, pady=(12, 4))

    prog = ttk.Progressbar(root, mode="indeterminate", length=500)
    prog.pack(padx=20, pady=4)
    prog.start(12)

    log = tk.Text(root, height=9, font=("SF Pro Mono", 10), bg="#f5f5f5",
                  fg="#333", relief="flat", state="disabled")
    log.pack(fill="both", expand=True, padx=20, pady=8)

    status_var = tk.StringVar(value="")
    tk.Label(root, textvariable=status_var, font=("SF Pro Text", 11),
             bg="white", fg="#888").pack(pady=(0, 10))

    success = [False]

    def _append(line: str):
        log.config(state="normal")
        log.insert("end", line + "\n")
        log.see("end")
        log.config(state="disabled")

    def _run():
        proc = subprocess.Popen(
            [sys.executable, "-m", "pip", "install"] + packages,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        for line in proc.stdout:
            root.after(0, lambda l=line.rstrip(): _append(l))
        proc.wait()
        success[0] = proc.returncode == 0
        root.after(0, _done)

    def _done():
        prog.stop()
        if success[0]:
            status_var.set("Done. Restarting Anduin…")
            root.after(800, _restart)
        else:
            prog.config(mode="determinate", value=0)
            status_var.set("Installation failed. Check the log above.")
            tk.Button(root, text="Quit", command=root.destroy,
                      relief="flat", bg="#1a1a2e", fg="white").pack(pady=8)

    def _restart():
        root.destroy()
        os.execv(sys.executable, [sys.executable] + sys.argv)

    import threading
    threading.Thread(target=_run, daemon=True).start()
    root.mainloop()

    if not success[0]:
        sys.exit(1)


def main():
    if not _version_ok():
        _show_version_error()
        sys.exit(1)

    missing = _missing_packages()
    if missing:
        _install_with_progress(missing)
        # If we reach here without restarting, installation failed
        sys.exit(1)

    # All deps present — add project to path and start the app
    sys.path.insert(0, str(PROJECT_DIR))
    from anduin.ui.menubar import main as run_app
    run_app()


if __name__ == "__main__":
    main()
