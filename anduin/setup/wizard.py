from __future__ import annotations
"""First-launch setup wizard. Runs as a blocking tkinter window before the menu bar app starts."""
import subprocess
import threading

from anduin.hardware.detect import detect as detect_hardware
from anduin.setup import models, ollama

# Lazy-loaded by run_wizard() — declared here so the class definition
# doesn't fail at import time in frozen builds where tkinter may not exist.
try:
    import tkinter as tk
    from tkinter import ttk
except ImportError:
    tk = None  # type: ignore
    ttk = None  # type: ignore

STEPS = ["Hardware", "Whisper", "Ollama", "Permissions", "Done"]

BG = "white"
ACCENT = "#1a1a2e"
ACCENT_FG = "white"
SUBTLE = "#f5f5f5"
MUTED = "#888"


class WizardApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Anduin Setup")
        self.resizable(False, False)
        self.configure(bg=BG)
        _center(self, 580, 540)

        self._hw = detect_hardware()
        self._step = 0

        self._build_chrome()
        self._show_step(0)

    # ── Chrome ────────────────────────────────────────────────────────────────

    def _build_chrome(self):
        hdr = tk.Frame(self, bg=ACCENT, height=56)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text="Anduin Setup", font=("SF Pro Display", 17, "bold"),
                 fg=ACCENT_FG, bg=ACCENT).pack(side="left", padx=20)

        self._pip = tk.Frame(self, bg=SUBTLE, height=32)
        self._pip.pack(fill="x")
        self._pip.pack_propagate(False)
        self._pip_labels: list[tk.Label] = []
        for name in STEPS:
            lbl = tk.Label(self._pip, text=name, font=("SF Pro Text", 10),
                           bg=SUBTLE, fg=MUTED, width=9)
            lbl.pack(side="left", expand=True)
            self._pip_labels.append(lbl)

        self._body = tk.Frame(self, bg=BG, padx=32, pady=20)
        self._body.pack(fill="both", expand=True)
        self.option_add("*Background", BG)
        self.option_add("*Foreground", "#333333")

        footer = tk.Frame(self, bg=SUBTLE, height=56)
        footer.pack(fill="x", side="bottom")
        footer.pack_propagate(False)

        style = ttk.Style(self)
        style.configure("Back.TButton", font=("SF Pro Text", 12))
        style.configure("Primary.TButton", font=("SF Pro Text", 12, "bold"))

        self._back_btn = ttk.Button(footer, text="Back", style="Back.TButton",
                                    command=self._go_back, width=9)
        self._back_btn.pack(side="left", padx=16, pady=12)
        self._next_btn = ttk.Button(footer, text="Continue", style="Primary.TButton",
                                    command=self._go_next, width=14)
        self._next_btn.pack(side="right", padx=16, pady=12)

    # ── Navigation ────────────────────────────────────────────────────────────

    def _show_step(self, index: int):
        for w in self._body.winfo_children():
            w.destroy()

        for i, lbl in enumerate(self._pip_labels):
            if i < index:
                lbl.config(fg=ACCENT, font=("SF Pro Text", 10, "bold"))
            elif i == index:
                lbl.config(fg="#4a90d9", font=("SF Pro Text", 10, "bold"))
            else:
                lbl.config(fg=MUTED, font=("SF Pro Text", 10))

        self._back_btn.config(state="normal" if index > 0 else "disabled")
        self._next_btn.config(text="Continue", command=self._go_next, state="normal")

        [self._step_hardware, self._step_whisper,
         self._step_ollama, self._step_screen_recording, self._step_done][index]()

    def _go_next(self):
        if self._step < len(STEPS) - 1:
            self._step += 1
            self._show_step(self._step)
        else:
            self.destroy()

    def _go_back(self):
        if self._step > 0:
            self._step -= 1
            self._show_step(self._step)

    # ── Steps ─────────────────────────────────────────────────────────────────

    def _step_hardware(self):
        hw = self._hw
        _h2(self._body, "Hardware Detected")
        _body(self._body, f"Chip: Apple {hw['chip']}   ·   RAM: {hw['ram_gb']} GB")
        _body(self._body, f"Transcription model: Whisper {hw['whisper_model']}")
        _body(self._body, f"Summarization model: {hw['llm_model']}")
        _spacer(self._body)
        _body(self._body, "You can override these in Settings after setup.", muted=True)

    def _step_whisper(self):
        model = self._hw["whisper_model"]
        size = models.WHISPER_SIZES.get(model, "~3 GB")
        _h2(self._body, "Transcription Model")
        _body(self._body, f"Whisper {model} ({size}) will be downloaded to your local cache.")
        _spacer(self._body)

        status_var = tk.StringVar(value="")
        file_var = tk.StringVar(value="")
        prog = ttk.Progressbar(self._body, mode="determinate", length=480, maximum=100)
        prog.pack(fill="x", pady=(4, 2))
        tk.Label(self._body, textvariable=status_var, bg=BG, fg="#333",
                 font=("SF Pro Text", 11)).pack(anchor="w")
        tk.Label(self._body, textvariable=file_var, bg=BG, fg=MUTED,
                 font=("SF Pro Mono", 10)).pack(anchor="w")

        if models.whisper_is_downloaded(model):
            prog.config(value=100)
            status_var.set(f"Whisper {model} is already downloaded.")
            return

        self._next_btn.config(state="disabled", text="Downloading…")

        def _progress(current: int, total: int, filename: str):
            pct = int(current / total * 100) if total else 0
            self.after(0, lambda: [
                prog.config(value=pct),
                status_var.set(f"Downloading… {current} / {total} files"),
                file_var.set(filename),
            ])

        def _run():
            try:
                models.download_whisper(model, progress=_progress)
                self.after(0, lambda: [
                    prog.config(value=100),
                    status_var.set("Download complete."),
                    file_var.set(""),
                    self._next_btn.config(state="normal", text="Continue"),
                ])
            except Exception as e:
                err = str(e)
                self.after(0, lambda: [
                    status_var.set(f"Error: {err}"),
                    file_var.set(""),
                    self._next_btn.config(state="disabled", text="Failed"),
                ])

        threading.Thread(target=_run, daemon=True).start()

    def _step_diarization(self):
        _h2(self._body, "Speaker Diarization")

        _body(self._body, "pyannote requires a free HuggingFace account and a Read token.\n"
              "Follow these three steps:")
        _spacer(self._body, 6)

        steps_frame = tk.Frame(self._body, bg=BG)
        steps_frame.pack(fill="x")

        def _step_row(n: str, label: str, url: str | None = None):
            row = tk.Frame(steps_frame, bg=BG)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=n, font=("SF Pro Text", 11, "bold"),
                     bg="#4a90d9", fg="white", width=2, padx=4).pack(side="left")
            tk.Label(row, text="  ", bg=BG).pack(side="left")
            if url:
                btn = tk.Label(row, text=label, font=("SF Pro Text", 11),
                               bg=BG, fg="#4a90d9", cursor="hand2")
                btn.pack(side="left")
                btn.bind("<Button-1>", lambda _e, u=url: _open(u))
            else:
                tk.Label(row, text=label, font=("SF Pro Text", 11),
                         bg=BG, fg="#333").pack(side="left")

        _step_row("1", "Create a free account at huggingface.co",
                  "https://huggingface.co/join")
        _step_row("2", "Accept: pyannote/speaker-diarization-3.1",
                  "https://huggingface.co/pyannote/speaker-diarization-3.1")
        _step_row("3", "Accept: pyannote/segmentation-3.0",
                  "https://huggingface.co/pyannote/segmentation-3.0")
        _step_row("4", "Accept: pyannote/speaker-diarization-community-1",
                  "https://huggingface.co/pyannote/speaker-diarization-community-1")
        _step_row("5", "Create a Read access token and paste it below",
                  "https://huggingface.co/settings/tokens/new?tokenType=read")

        _spacer(self._body, 8)
        tk.Label(self._body, text="HuggingFace token:", bg=BG,
                 font=("SF Pro Text", 11)).pack(anchor="w")
        token_var = tk.StringVar()
        entry = tk.Entry(self._body, textvariable=token_var, width=44, show="•",
                         font=("SF Pro Mono", 11), bg="white", fg="#1a1a2e",
                         insertbackground="#1a1a2e", relief="solid", bd=1)
        entry.pack(anchor="w", pady=(2, 6))

        status_var = tk.StringVar(value="")
        file_var = tk.StringVar(value="")
        prog = ttk.Progressbar(self._body, mode="determinate", length=480, maximum=100)
        prog.pack(fill="x", pady=(2, 2))
        tk.Label(self._body, textvariable=status_var, bg=BG, fg="#333",
                 font=("SF Pro Text", 11)).pack(anchor="w")
        tk.Label(self._body, textvariable=file_var, bg=BG, fg=MUTED,
                 font=("SF Pro Mono", 10)).pack(anchor="w")

        if models.pyannote_is_downloaded() and models.get_hf_token():
            prog.config(value=100)
            status_var.set("Already downloaded. Token stored in Keychain.")
            return

        self._next_btn.config(text="Download", command=lambda: _start_download())

        def _start_download():
            token = token_var.get().strip()
            if not token:
                status_var.set("Please enter your HuggingFace token.")
                return
            self._next_btn.config(state="disabled", text="Downloading…")

            def _progress(current: int, total: int, filename: str):
                pct = int(current / total * 100) if total else 0
                self.after(0, lambda: [
                    prog.config(value=pct),
                    status_var.set(f"Downloading… {current} / {total} files"),
                    file_var.set(filename),
                ])

            def _run():
                try:
                    models.download_pyannote(token, progress=_progress)
                    self.after(0, lambda: [
                        prog.config(value=100),
                        status_var.set("Done. Token stored securely in Keychain."),
                        file_var.set(""),
                        self._next_btn.config(state="normal", text="Continue",
                                              command=self._go_next),
                    ])
                except Exception as e:
                    err = str(e)
                    self.after(0, lambda: [
                        status_var.set(f"Error: {err}"),
                        file_var.set(""),
                        self._next_btn.config(state="normal", text="Try Again",
                                              command=lambda: _start_download()),
                    ])

            threading.Thread(target=_run, daemon=True).start()

    def _step_ollama(self):
        model = self._hw["llm_model"]
        _h2(self._body, "Local LLM (Ollama)")

        if not ollama.is_installed():
            _body(self._body, "Ollama is not installed.")
            _spacer(self._body, 6)
            tk.Label(self._body, text="↓  Download and install Ollama, then click Check Again.",
                     bg=BG, fg="#333", font=("SF Pro Text", 11),
                     justify="left").pack(anchor="w")
            _spacer(self._body, 4)
            ttk.Button(self._body, text="Download Ollama",
                       command=lambda: _open("https://ollama.com/download/mac")).pack(anchor="w", pady=2)
            ttk.Button(self._body, text="Check Again",
                       command=lambda: self._show_step(self._step)).pack(anchor="w", pady=2)
            self._next_btn.config(state="disabled")
            return

        _body(self._body, f"Ollama is installed. Pulling {model}…")
        status_var = tk.StringVar(value="")
        prog = ttk.Progressbar(self._body, mode="determinate", length=480, maximum=100)
        prog.pack(fill="x", pady=8)
        tk.Label(self._body, textvariable=status_var, bg=BG, fg=MUTED,
                 font=("SF Pro Text", 11)).pack(anchor="w")

        if ollama.model_is_pulled(model):
            prog.config(value=100)
            status_var.set(f"{model} is already available.")
            return

        self._next_btn.config(state="disabled", text="Pulling…")

        def _run():
            try:
                ollama.ensure_running()

                def _prog(done, total):
                    pct = int(done / total * 100) if total else 0
                    mb_done = done // 1_000_000
                    mb_total = total // 1_000_000
                    self.after(0, lambda: [
                        prog.config(value=pct),
                        status_var.set(f"{mb_done} / {mb_total} MB"),
                    ])

                ollama.pull_model(model, progress=_prog)
                self.after(0, lambda: [
                    prog.config(value=100),
                    status_var.set("Done."),
                    self._next_btn.config(state="normal", text="Continue"),
                ])
            except Exception as e:
                err = str(e)
                self.after(0, lambda: [
                    status_var.set(f"Error: {err}"),
                    self._next_btn.config(state="disabled", text="Failed"),
                ])

        threading.Thread(target=_run, daemon=True).start()

    def _step_screen_recording(self):
        _h2(self._body, "Digital Meetings (Optional)")
        _body(self._body, "To record Zoom, Teams, or any app's audio alongside your mic,\n"
              "Anduin uses macOS Screen Recording to capture system audio.")
        _spacer(self._body, 8)
        _body(self._body, "No screen content is recorded — only audio is captured.", muted=True)
        _spacer(self._body, 8)

        status_var = tk.StringVar(value="")
        tk.Label(self._body, textvariable=status_var, bg=BG, fg="#333",
                 font=("SF Pro Text", 11)).pack(anchor="w", pady=(0, 8))

        def _check():
            try:
                from anduin.capture.system_audio import has_permission
                if has_permission():
                    status_var.set("Permission granted.")
                else:
                    status_var.set("Permission not yet granted. Click the button below.")
            except Exception as e:
                status_var.set(f"Could not check: {e}")

        def _request():
            status_var.set("Requesting permission… A macOS dialog should appear.")
            def _run():
                try:
                    from anduin.capture.system_audio import request_permission
                    granted = request_permission()
                    self.after(0, lambda: status_var.set(
                        "Permission granted." if granted
                        else "Permission denied. Grant it in System Settings → Privacy & Security → Screen Recording."
                    ))
                except Exception as e:
                    self.after(0, lambda: status_var.set(f"Error: {e}"))
            threading.Thread(target=_run, daemon=True).start()

        ttk.Button(self._body, text="Grant Screen Recording Permission",
                   command=_request).pack(anchor="w", pady=2)
        _spacer(self._body, 4)
        ttk.Button(self._body, text="Open System Settings",
                   command=lambda: subprocess.run(
                       ["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"]
                   )).pack(anchor="w", pady=2)

        _spacer(self._body, 8)
        _body(self._body, "You can skip this step — it's only needed for digital meetings.\n"
              "In-person meetings use only the microphone and work without this.", muted=True)

        _check()

    def _step_done(self):
        _h2(self._body, "All Set!")
        _body(self._body, "Anduin is ready. It will appear as an icon in your menu bar.")
        _spacer(self._body)
        _body(self._body, "You can re-run this wizard any time from Settings.", muted=True)
        self._next_btn.config(text="Launch Anduin")


# ── Public API ─────────────────────────────────────────────────────────────────

def run_wizard():
    import tkinter
    import tkinter.ttk
    # Inject into module globals so WizardApp and helpers can use them
    global tk, ttk
    tk = tkinter
    ttk = tkinter.ttk
    app = WizardApp()
    app.mainloop()


def is_setup_complete() -> bool:
    """Check whether first-time setup has been done.

    Only checks persistent state (downloaded files, stored tokens, installed
    binaries) — NOT whether runtime services like Ollama are currently running,
    since those are started later by the app.
    """
    hw = detect_hardware()
    if not models.whisper_is_downloaded(hw["whisper_model"]):
        return False
    if not ollama.is_installed():
        return False
    # Check if the Ollama model has been pulled by looking at the local
    # model manifest, without requiring the Ollama server to be running.
    if not _ollama_model_exists(hw["llm_model"]):
        return False
    return True


def _ollama_model_exists(model: str) -> bool:
    """Check if an Ollama model is available locally (without hitting the API).

    Falls back to the API check if the manifest path isn't found — covers
    custom OLLAMA_MODELS paths or future Ollama layout changes.
    """
    from pathlib import Path
    import os
    models_dir = Path(os.environ.get(
        "OLLAMA_MODELS",
        Path.home() / ".ollama" / "models",
    ))
    # Ollama stores models as manifests under models/manifests/registry.ollama.ai/library/<name>/<tag>
    name = model.split(":")[0] if ":" in model else model
    tag = model.split(":")[1] if ":" in model else "latest"
    manifest = models_dir / "manifests" / "registry.ollama.ai" / "library" / name / tag
    if manifest.exists():
        return True
    # Fallback: try the API if Ollama happens to be running
    return ollama.model_is_pulled(model)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _h2(parent: tk.Frame, text: str):
    tk.Label(parent, text=text, font=("SF Pro Display", 16, "bold"),
             bg=BG, anchor="w").pack(fill="x", pady=(0, 6))


def _body(parent: tk.Frame, text: str, muted: bool = False):
    tk.Label(parent, text=text, font=("SF Pro Text", 12),
             bg=BG, fg=MUTED if muted else "#333",
             anchor="w", justify="left", wraplength=500).pack(fill="x", pady=1)


def _spacer(parent: tk.Frame, height: int = 8):
    tk.Frame(parent, bg=BG, height=height).pack()


def _center(win: tk.Tk, w: int, h: int):
    win.geometry(f"{w}x{h}+{(win.winfo_screenwidth()-w)//2}+{(win.winfo_screenheight()-h)//2}")


def _open(target: str):
    subprocess.run(["open", target])
