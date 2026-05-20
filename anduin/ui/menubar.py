from __future__ import annotations
"""Main entry point. Run with: python -m anduin.ui.menubar"""
import json
import queue
import subprocess
import threading
from datetime import datetime
from pathlib import Path

import AppKit
import rumps

from anduin.capture.devices import device_for_mode
from anduin.capture.recorder import Recorder
from anduin.hardware.detect import detect as detect_hardware
from anduin.pipeline import run as run_pipeline
from anduin.setup import ollama
from anduin.setup.wizard import is_setup_complete, run_wizard
from anduin.storage import store
from anduin.ui.server import EventBus, start_server
from anduin.ui.webview import AnduinWindow
from anduin.ui.windows import _notify, prompt_speaker_names

MAX_RECENT = 5


class AnduinApp(rumps.App):
    def __init__(self):
        super().__init__("Anduin", quit_button=None)
        self.title = None # Logo only
        self._recorder = Recorder()
        self._hw = detect_hardware()
        
        # Set menu bar icon
        self._logo_path = str(Path(__file__).resolve().parent.parent / "web" / "logo.svg")
        if Path(self._logo_path).exists():
            self.icon = self._logo_path
            self.template = True  # Makes icon white/black depending on macOS theme
        
        # Command queue for thread-safe UI updates
        self._cmd_queue = queue.Queue()
        self._cmd_timer = rumps.Timer(self._process_cmds, 0.1)
        self._cmd_timer.start()

        # Pulse timer for recording (fast for smooth opacity changes)
        self._pulse_timer = rumps.Timer(self._pulse_tick, 0.05)
        self._pulse_index = 0

        # UI Server & Window
        self._event_bus = EventBus()
        self._server, self._port = start_server(self._event_bus)
        self._server._app = self  # Link app to server for API calls
        self._window = AnduinWindow(self._port)
        
        self._build_menu()
        self._start_ollama_async()
        
        # Auto-open on launch
        self._window.open()

    def _process_cmds(self, _):
        """Process pending UI commands on the main thread."""
        try:
            while True:
                cmd, args = self._cmd_queue.get_nowait()
                if cmd == "record":
                    self._start_recording(args or "inperson")
                elif cmd == "stop":
                    self._stop_recording(None)
                elif cmd == "refresh_recent":
                    self._refresh_recent()
        except queue.Empty:
            pass

    def _pulse_tick(self, _):
        # Directly adjust the alpha value of the status item button for a smooth pulse
        try:
            status_item = None
            if hasattr(self, "_nsapp"):
                if hasattr(self._nsapp, "statusitem"):
                    status_item = self._nsapp.statusitem
                elif hasattr(self._nsapp, "statusItem"):
                    status_item = self._nsapp.statusItem()
            
            if status_item:
                import math
                self._pulse_index = (self._pulse_index + 1) % 24
                alpha = 0.7 + 0.3 * math.cos(2 * math.pi * self._pulse_index / 24)
                status_item.button().setAlphaValue_(alpha)
        except Exception:
            pass

    # ── Menu construction ──────────────────────────────────────────────────────

    def _build_menu(self):
        self.menu = [
            rumps.MenuItem("Open Anduin", callback=self._open_window),
            None,
            rumps.MenuItem("Record Meeting…", callback=self._record),
            rumps.MenuItem("Stop Recording", callback=self._stop_recording),
            None,
            rumps.MenuItem("Process Audio File…", callback=self._process_file),
            None,
            rumps.MenuItem("Recent Meetings", callback=None),
            None,
            rumps.MenuItem("Settings…", callback=self._open_settings),
            rumps.MenuItem("Quit", callback=self._quit),
        ]
        self.menu["Stop Recording"].set_callback(None)
        self._refresh_recent()

    def _quit(self, _):
        import os

        if self._recorder.is_recording:
            try:
                self._recorder.stop(self._tmp_audio)
            except Exception:
                pass

        # Unload models to free resources
        try:
            from anduin.transcription.whisper import unload as unload_whisper
            unload_whisper()
        except Exception:
            pass
        try:
            from anduin.diarization.diarizer import unload as unload_diarizer
            unload_diarizer()
        except Exception:
            pass

        # Shutdown HTTP server — short timeout
        if hasattr(self, "_server"):
            t = threading.Thread(target=lambda: self._server.shutdown(), daemon=True)
            t.start()
            t.join(timeout=0.5)

        # Fire off Ollama shutdown (non-blocking)
        threading.Thread(target=self._stop_ollama, daemon=True).start()

        # Hard exit to avoid Tcl/AppKit cleanup crash.
        # All our cleanup is done above; os._exit skips Python's
        # finalization which triggers the "Tcl_FindHashEntry" abort.
        rumps.quit_application()
        os._exit(0)

    def _open_window(self, _):
        self._window.open()

    def _refresh_recent(self):
        meetings = store.list_meetings(limit=MAX_RECENT)
        recent_menu = self.menu["Recent Meetings"]
        if recent_menu._menu is not None:
            recent_menu.clear()
        if not meetings:
            recent_menu.add(rumps.MenuItem("No meetings yet", callback=None))
        else:
            for m in meetings:
                title = f"{m['date']}  {m['title']}"
                path = m["path"]
                recent_menu.add(rumps.MenuItem(title, callback=lambda _, p=path: _open_folder(p)))
            recent_menu.add(None)
            recent_menu.add(rumps.MenuItem("Show All…", callback=lambda _: _open_folder(str(store.MEETINGS_DIR))))

    # ── Recording ─────────────────────────────────────────────────────────────

    @rumps.clicked("Record Meeting…")
    def _record(self, _):
        # Open the app window and show the record modal
        self._window.open()
        self._window.evaluate_js("showRecordModal()")

    def _start_recording(self, mode: str):
        """Begin recording with the given mode (called from web UI via API)."""
        if self._recorder.is_recording:
            return
        # Save to a temp path; meeting dir is created after the user names the meeting
        self._tmp_audio = store.APP_DIR / "recording_tmp.wav"
        self._recorder.start(device=device_for_mode(mode))
        self._pulse_timer.start()
        self.menu["Record Meeting…"].set_callback(None)
        self.menu["Stop Recording"].set_callback(self._stop_recording)
        self._event_bus.publish("recording", {"active": True})
        self._server._app_status = {"recording": True, "pipeline_stage": None}

    def _stop_recording(self, _):
        if not self._recorder.is_recording:
            return
        
        # Stop pulsing immediately on main thread
        self._pulse_timer.stop()
        try:
            status_item = None
            if hasattr(self, "_nsapp"):
                if hasattr(self._nsapp, "statusitem"):
                    status_item = self._nsapp.statusitem
                elif hasattr(self._nsapp, "statusItem"):
                    status_item = self._nsapp.statusItem()
            if status_item:
                status_item.button().setAlphaValue_(1.0)
        except Exception:
            pass
            
        self.icon = self._logo_path
        self.title = None
        self.menu["Record Meeting…"].set_callback(self._record)
        self.menu["Stop Recording"].set_callback(None)
        self._event_bus.publish("recording", {"active": False})
        self._server._app_status = {"recording": False, "pipeline_stage": None}

        def _finish():
            # Stop the recorder and save file (can be slow)
            tmp_path = self._recorder.stop(self._tmp_audio)
            
            # Ask for title (blocks this background thread, but not UI)
            default_title = datetime.now().strftime("%Y-%m-%d %H:%M")
            title = _ask_text("Name this meeting:", default=default_title)
            if title is None:
                title = default_title

            # Start pipeline
            self._run_pipeline_async(tmp_path, title)

        threading.Thread(target=_finish, daemon=True).start()

    # ── File processing ───────────────────────────────────────────────────────

    @rumps.clicked("Process Audio File…")
    def _process_file(self, _):
        result = subprocess.run(
            ["osascript", "-e",
             'POSIX path of (choose file with prompt "Select audio or video file:" '
             'of type {"wav","mp3","m4a","mp4","mov","mkv","webm","m4v"})'],
            capture_output=True, text=True,
        )
        path_str = result.stdout.strip()
        if not path_str:
            return

        audio_path = Path(path_str)
        default_title = datetime.now().strftime("%Y-%m-%d %H:%M")
        title = _ask_text("Meeting title:", default=default_title)
        if title is None:
            return

        self._run_pipeline_async(audio_path, title)

    # ── Pipeline ──────────────────────────────────────────────────────────────

    def _run_pipeline_async(self, audio_path: Path, title: str):
        def _progress(stage: str, msg: str):
            print(f"[pipeline] {stage}: {msg}", flush=True)
            self.title = None # Keep logo only
            self._event_bus.publish("pipeline", {"stage": stage, "message": msg})

        def _run():
            try:
                print(f"[pipeline] starting: {audio_path}", flush=True)
                known_speakers = set(store.get_speaker_names().keys())
                
                auto_summarize = store.get_config("auto_summarize", True)
                
                out_dir = run_pipeline(
                    audio_path=audio_path,
                    title=title,
                    mode="hybrid",
                    auto_summarize=auto_summarize,
                    progress=_progress,
                )
                self.title = None # Keep logo only
                print(f"[pipeline] done: {out_dir}", flush=True)

                # Clean up temp recording file
                tmp = store.APP_DIR / "recording_tmp.wav"
                if tmp.exists():
                    try:
                        tmp.unlink()
                    except Exception:
                        pass

                self._refresh_recent()

            except Exception as e:
                import traceback
                print(f"[pipeline] ERROR: {e}", flush=True)
                traceback.print_exc()
                self.title = None # Keep logo only
                self._event_bus.publish("error", {"message": str(e)})

        threading.Thread(target=_run, daemon=True).start()

    # ── Settings ──────────────────────────────────────────────────────────────

    @rumps.clicked("Settings…")
    def _open_settings(self, _):
        # Open the app window and navigate to the settings panel
        self._window.open()
        self._window.evaluate_js("showSettings()")

    # ── Ollama lifecycle ──────────────────────────────────────────────────────

    def _start_ollama_async(self):
        def _run():
            if not store.get_config("manage_ollama", True):
                return
            try:
                import time
                time.sleep(3)  # let the app finish launching first
                ollama.ensure_running()
                print("[ollama] server ready (no model preloaded)", flush=True)
            except Exception as e:
                print(f"[ollama] startup warning: {e}", flush=True)

        threading.Thread(target=_run, daemon=True).start()

    def _stop_ollama(self):
        """Stop Ollama if we started it. No model to unload — keep_alive=0
        ensures models are freed immediately after each summarization."""
        if not store.get_config("manage_ollama", True):
            return
        try:
            ollama.stop_server()
            print("[ollama] stopped", flush=True)
        except Exception as e:
            print(f"[ollama] shutdown warning: {e}", flush=True)


# ── osascript dialog helpers ──────────────────────────────────────────────────

def _ask_text(prompt: str, default: str = "") -> str | None:
    """Show a text-input dialog via osascript. Returns text or None if cancelled."""
    safe_prompt = prompt.replace('"', '\\"')
    safe_default = default.replace('"', '\\"')
    script = (
        f'display dialog "{safe_prompt}" default answer "{safe_default}" '
        f'buttons {{"Cancel", "OK"}} default button "OK"'
    )
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if result.returncode != 0:
        return None
    for part in result.stdout.strip().split(", "):
        if part.startswith("text returned:"):
            text = part[len("text returned:"):].strip()
            return text or default
    return default


def _ask_choice(prompt: str, buttons: list[str], default: str) -> str | None:
    """Show a button-choice dialog via osascript. Returns button label or None if cancelled."""
    safe_prompt = prompt.replace('"', '\\"')
    btn_list = "{" + ", ".join(f'"{b}"' for b in buttons) + "}"
    script = (
        f'display dialog "{safe_prompt}" buttons {btn_list} default button "{default}"'
    )
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if result.returncode != 0:
        return None
    out = result.stdout.strip()
    if out.startswith("button returned:"):
        return out[len("button returned:"):].strip()
    return None


def _open_folder(path: str):
    subprocess.run(["open", path])


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    store.init()

    if not is_setup_complete():
        run_wizard()

    # The setup wizard uses tkinter, which leaves Tcl async handlers in memory.
    # If those survive into threaded pipeline work (pyannote/whisper), Tcl aborts
    # with "Tcl_AsyncDelete: async handler deleted by the wrong thread".
    # Purge all tkinter/Tcl state now — before rumps starts and threads are spawned.
    import sys
    for mod in list(sys.modules):
        if mod.startswith(("tkinter", "_tkinter")):
            del sys.modules[mod]

    AnduinApp().run()


if __name__ == "__main__":
    main()
