from __future__ import annotations
"""First-launch setup wizard. Runs as a blocking tkinter window before the menu bar app starts."""
import subprocess
import threading

from anduin.hardware.detect import detect as detect_hardware
from anduin.setup import models, ollama

try:
    import tkinter as tk
    from tkinter import ttk
except ImportError:
    tk = None  # type: ignore
    ttk = None  # type: ignore

STEPS = ["Setup", "Whisper", "Ollama", "Permissions", "Ready"]

# ── Colors matching Anduin's palette ─────────────────────────────────────────
BG = "#F6F4EE"       # color-paper
NAVY = "#0E1B2E"     # color-navy
STONE = "#1B1B1F"    # color-stone
SLATE = "#4A4E55"    # color-slate
BORDER = "#E5E2D9"   # color-border
WHITE = "#FFFFFF"
ACCENT_GREEN = "#34c759"


class WizardApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Anduin")
        self.resizable(False, False)
        self.configure(bg=BG)
        _center(self, 520, 440)

        self._hw = detect_hardware()
        self._step = 0

        self._build_chrome()
        self._show_step(0)

    def _build_chrome(self):
        # Progress dots at top
        self._dot_frame = tk.Frame(self, bg=BG, pady=16)
        self._dot_frame.pack(fill="x")
        self._dots = []
        dot_container = tk.Frame(self._dot_frame, bg=BG)
        dot_container.pack()
        for i in range(len(STEPS)):
            c = tk.Canvas(dot_container, width=10, height=10, bg=BG,
                          highlightthickness=0)
            c.pack(side="left", padx=4)
            self._dots.append(c)

        # Body
        self._body = tk.Frame(self, bg=BG, padx=48, pady=0)
        self._body.pack(fill="both", expand=True)

        # Footer with single button
        footer = tk.Frame(self, bg=BG, pady=20)
        footer.pack(fill="x")
        self._next_btn = tk.Button(
            footer, text="Continue", font=("SF Pro Text", 13, "bold"),
            bg=NAVY, fg=WHITE, activebackground=STONE, activeforeground=WHITE,
            relief="flat", padx=32, pady=10, cursor="hand2",
            command=self._go_next,
        )
        self._next_btn.pack()

    def _update_dots(self, index):
        for i, c in enumerate(self._dots):
            c.delete("all")
            if i < index:
                c.create_oval(1, 1, 9, 9, fill=NAVY, outline="")
            elif i == index:
                c.create_oval(0, 0, 10, 10, fill=NAVY, outline="")
            else:
                c.create_oval(1, 1, 9, 9, fill=BORDER, outline="")

    def _show_step(self, index):
        for w in self._body.winfo_children():
            w.destroy()

        self._update_dots(index)
        self._next_btn.config(text="Continue", command=self._go_next, state="normal")

        [self._step_hardware, self._step_whisper,
         self._step_ollama, self._step_screen_recording, self._step_done][index]()

    def _go_next(self):
        if self._step < len(STEPS) - 1:
            self._step += 1
            self._show_step(self._step)
        else:
            self.destroy()

    # ── Steps ─────────────────────────────────────────────────────────────────

    def _step_hardware(self):
        hw = self._hw
        _title(self._body, "Welcome to Anduin")
        _spacer(self._body, 8)
        _subtitle(self._body, "We detected your hardware and selected\nthe best models for your machine.")
        _spacer(self._body, 24)

        info = tk.Frame(self._body, bg=WHITE, highlightbackground=BORDER,
                        highlightthickness=1, padx=20, pady=16)
        info.pack(fill="x")
        _info_row(info, "Chip", f"Apple {hw['chip']}")
        _info_row(info, "RAM", f"{hw['ram_gb']} GB")
        _info_row(info, "Transcription", f"Whisper {hw['whisper_model']}")
        _info_row(info, "Summarization", hw['llm_model'])

    def _step_whisper(self):
        model = self._hw["whisper_model"]
        size = models.WHISPER_SIZES.get(model, "~3 GB")
        _title(self._body, "Downloading Whisper")
        _spacer(self._body, 4)
        _subtitle(self._body, f"Whisper {model} ({size})")
        _spacer(self._body, 24)

        prog = ttk.Progressbar(self._body, mode="determinate", length=400, maximum=100)
        prog.pack(fill="x", pady=(0, 8))
        status_var = tk.StringVar(value="")
        tk.Label(self._body, textvariable=status_var, bg=BG, fg=SLATE,
                 font=("SF Pro Text", 11)).pack(anchor="w")

        if models.whisper_is_downloaded(model):
            prog.config(value=100)
            status_var.set("Already downloaded.")
            return

        self._next_btn.config(state="disabled", text="Downloading…")

        def _progress(current_bytes: int, total_bytes: int, filename: str):
            if total_bytes > 0:
                pct = int(current_bytes / total_bytes * 100)
                mb_done = current_bytes / 1_000_000
                mb_total = total_bytes / 1_000_000
                self.after(0, lambda: [
                    prog.config(value=pct),
                    status_var.set(f"{mb_done:.0f} / {mb_total:.0f} MB — {filename}"),
                ])
            else:
                self.after(0, lambda: status_var.set(f"Downloading {filename}…"))

        def _run():
            try:
                models.download_whisper_with_progress(model, progress=_progress)
                self.after(0, lambda: [
                    prog.config(value=100),
                    status_var.set("Complete"),
                    self._next_btn.config(state="normal", text="Continue"),
                ])
            except Exception as e:
                err = str(e)
                self.after(0, lambda: [
                    status_var.set(f"Error: {err}"),
                    self._next_btn.config(state="normal", text="Retry",
                                          command=lambda: self._show_step(self._step)),
                ])

        threading.Thread(target=_run, daemon=True).start()

    def _step_ollama(self):
        model = self._hw["llm_model"]
        _title(self._body, "Downloading LLM")
        _spacer(self._body, 4)
        _subtitle(self._body, model)
        _spacer(self._body, 24)

        if not ollama.is_installed():
            _subtitle(self._body, "Ollama is not installed.")
            _spacer(self._body, 12)
            btn = tk.Button(
                self._body, text="Download Ollama", font=("SF Pro Text", 12),
                bg=NAVY, fg=WHITE, activebackground=STONE, activeforeground=WHITE,
                relief="flat", padx=20, pady=8, cursor="hand2",
                command=lambda: _open("https://ollama.com/download/mac"),
            )
            btn.pack()
            _spacer(self._body, 8)
            retry = tk.Button(
                self._body, text="I've installed it", font=("SF Pro Text", 11),
                bg=BG, fg=SLATE, activebackground=BG, activeforeground=STONE,
                relief="flat", cursor="hand2",
                command=lambda: self._show_step(self._step),
            )
            retry.pack()
            self._next_btn.config(state="disabled")
            return

        prog = ttk.Progressbar(self._body, mode="determinate", length=400, maximum=100)
        prog.pack(fill="x", pady=(0, 8))
        status_var = tk.StringVar(value="")
        tk.Label(self._body, textvariable=status_var, bg=BG, fg=SLATE,
                 font=("SF Pro Text", 11)).pack(anchor="w")

        if ollama.model_is_pulled(model):
            prog.config(value=100)
            status_var.set("Already downloaded.")
            return

        self._next_btn.config(state="disabled", text="Downloading…")

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
                    status_var.set("Complete"),
                    self._next_btn.config(state="normal", text="Continue"),
                ])
            except Exception as e:
                err = str(e)
                self.after(0, lambda: [
                    status_var.set(f"Error: {err}"),
                    self._next_btn.config(state="normal", text="Retry",
                                          command=lambda: self._show_step(self._step)),
                ])

        threading.Thread(target=_run, daemon=True).start()

    def _step_screen_recording(self):
        _title(self._body, "Screen Recording")
        _spacer(self._body, 4)
        _subtitle(self._body, "Required for recording digital meetings.\nOnly audio is captured — no screen content.")
        _spacer(self._body, 24)

        status_var = tk.StringVar(value="Checking…")
        tk.Label(self._body, textvariable=status_var, bg=BG, fg=SLATE,
                 font=("SF Pro Text", 11)).pack(anchor="center")
        _spacer(self._body, 12)

        grant_btn = tk.Button(
            self._body, text="Grant Permission", font=("SF Pro Text", 12, "bold"),
            bg=NAVY, fg=WHITE, activebackground=STONE, activeforeground=WHITE,
            relief="flat", padx=24, pady=8, cursor="hand2",
        )

        def _check():
            try:
                from anduin.capture.system_audio import has_permission
                if has_permission():
                    status_var.set("Permission granted.")
                    grant_btn.pack_forget()
                    # Auto-advance after a short delay
                    self.after(800, self._go_next)
                else:
                    status_var.set("Not yet granted.")
                    grant_btn.pack()
            except Exception:
                status_var.set("Skipped — grant later in System Settings.")

        def _request():
            status_var.set("A system dialog should appear…")
            grant_btn.config(state="disabled")
            def _run():
                try:
                    from anduin.capture.system_audio import request_permission
                    granted = request_permission()
                    self.after(0, lambda: [
                        status_var.set("Permission granted." if granted else "Denied — you can grant it later in System Settings."),
                        grant_btn.config(state="normal"),
                    ])
                    if granted:
                        self.after(800, self._go_next)
                except Exception:
                    self.after(0, lambda: status_var.set("Skipped."))
            threading.Thread(target=_run, daemon=True).start()

        grant_btn.config(command=_request)
        _check()

    def _step_done(self):
        _spacer(self._body, 40)
        _title(self._body, "Ready to go")
        _spacer(self._body, 8)
        _subtitle(self._body, "Anduin will appear in your menu bar.")
        _spacer(self._body, 40)

        self._next_btn.config(
            text="Launch Anduin",
            font=("SF Pro Text", 14, "bold"),
            padx=40, pady=12,
        )


# ── Public API ────────────────────────────────────────────────────────────────

def run_wizard():
    import tkinter
    import tkinter.ttk
    global tk, ttk
    tk = tkinter
    ttk = tkinter.ttk
    app = WizardApp()
    app.mainloop()


def is_setup_complete() -> bool:
    hw = detect_hardware()
    if not models.whisper_is_downloaded(hw["whisper_model"]):
        return False
    if not ollama.is_installed():
        return False
    if not _ollama_model_exists(hw["llm_model"]):
        return False
    return True


def _ollama_model_exists(model: str) -> bool:
    from pathlib import Path
    import os
    models_dir = Path(os.environ.get(
        "OLLAMA_MODELS",
        Path.home() / ".ollama" / "models",
    ))
    name = model.split(":")[0] if ":" in model else model
    tag = model.split(":")[1] if ":" in model else "latest"
    manifest = models_dir / "manifests" / "registry.ollama.ai" / "library" / name / tag
    if manifest.exists():
        return True
    return ollama.model_is_pulled(model)


# ── UI Helpers ────────────────────────────────────────────────────────────────

def _title(parent, text: str):
    tk.Label(parent, text=text, font=("SF Pro Display", 22, "bold"),
             bg=BG, fg=STONE, anchor="center").pack(fill="x")


def _subtitle(parent, text: str):
    tk.Label(parent, text=text, font=("SF Pro Text", 13),
             bg=BG, fg=SLATE, anchor="center", justify="center",
             wraplength=400).pack(fill="x")


def _info_row(parent, label: str, value: str):
    row = tk.Frame(parent, bg=WHITE)
    row.pack(fill="x", pady=3)
    tk.Label(row, text=label, font=("SF Pro Text", 12),
             bg=WHITE, fg=SLATE, width=14, anchor="w").pack(side="left")
    tk.Label(row, text=value, font=("SF Pro Text", 12, "bold"),
             bg=WHITE, fg=STONE, anchor="w").pack(side="left")


def _spacer(parent, height: int = 8):
    tk.Frame(parent, bg=BG, height=height).pack()


def _center(win, w: int, h: int):
    win.geometry(f"{w}x{h}+{(win.winfo_screenwidth()-w)//2}+{(win.winfo_screenheight()-h)//2}")


def _open(target: str):
    subprocess.run(["open", target])
