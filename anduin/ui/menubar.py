from __future__ import annotations
"""Main entry point. Run with: python -m anduin.ui.menubar"""
import queue
import subprocess
import threading
from datetime import datetime
from pathlib import Path

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
            rumps.MenuItem("Settings…", callback=self._open_settings),
            rumps.MenuItem("Check for Updates…", callback=self._check_updates),
            rumps.MenuItem("Quit", callback=self._quit),
        ]
        self.menu["Stop Recording"].set_callback(None)

        # Check for updates in background on launch
        self._check_updates_async()

    def _quit(self, _):
        import os

        # Hide the window immediately so it feels instant
        if hasattr(self, "_window"):
            self._window.close()

        # Fire all cleanup in daemon threads so we don't block
        def _cleanup():
            try:
                if self._recorder.is_recording:
                    self._recorder.stop(self._tmp_audio)
            except Exception:
                pass
            try:
                from anduin.transcription.whisper import unload as unload_whisper
                unload_whisper()
            except Exception:
                pass
            try:
                self._stop_ollama()
            except Exception:
                pass
            try:
                if hasattr(self, "_server"):
                    self._server.shutdown()
            except Exception:
                pass

        threading.Thread(target=_cleanup, daemon=True).start()

        # Exit immediately — cleanup runs in background
        rumps.quit_application()
        os._exit(0)

    def _open_window(self, _):
        self._window.open()

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
        if mode == "digital":
            self._recorder.start(mode="digital")
        else:
            self._recorder.start(device=device_for_mode(mode), mode="inperson")
        self._pulse_timer.start()
        self.title = "Recording"
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
        self._event_bus.publish("pipeline", {"stage": "saving", "message": "Saving recording..."})
        self._server._app_status = {"recording": False, "pipeline_stage": "saving"}

        def _finish():
            tmp_path = self._recorder.stop(self._tmp_audio)
            title = datetime.now().strftime("%Y-%m-%d %H:%M")
            self._run_pipeline_async(tmp_path, title)

        threading.Thread(target=_finish, daemon=True).start()

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
                    auto_summarize=auto_summarize,
                    progress=_progress,
                )
                self.title = None # Keep logo only

                # Clean up temp recording file
                tmp = store.APP_DIR / "recording_tmp.wav"
                if tmp.exists():
                    try:
                        tmp.unlink()
                    except Exception:
                        pass

            except Exception as e:
                import traceback
                print(f"[pipeline] ERROR: {e}", flush=True)
                traceback.print_exc()
                self.title = None # Keep logo only
                self._event_bus.publish("app_error", {"message": str(e)})

        threading.Thread(target=_run, daemon=True).start()

    # ── Settings ──────────────────────────────────────────────────────────────

    @rumps.clicked("Settings…")
    def _open_settings(self, _):
        # Open the app window and navigate to the settings panel
        self._window.open()
        self._window.evaluate_js("showSettings()")

    # ── Updates ───────────────────────────────────────────────────────────────

    def _check_updates_async(self):
        """Background update check on launch — non-intrusive."""
        def _run():
            import time
            time.sleep(5)  # Let the app finish launching first
            try:
                from anduin.updater import check_for_update
                update = check_for_update()
                if update:
                    version = update["version"]
                    print(f"[updater] update available: v{version}", flush=True)
                    self._pending_update = update
                    self._event_bus.publish("update_available", {"version": version})
            except Exception as e:
                print(f"[updater] background check failed: {e}", flush=True)

        threading.Thread(target=_run, daemon=True).start()

    def _check_updates(self, _):
        """Manual update check from the menu."""
        def _run():
            try:
                from anduin.updater import check_for_update, download_and_apply, current_version
                self._event_bus.publish("pipeline", {"stage": "update", "message": "Checking for updates..."})
                update = check_for_update()
                if update:
                    version = update["version"]
                    def _progress(downloaded, total):
                        if total > 0:
                            pct = round(downloaded / total * 100)
                            self._event_bus.publish("pipeline", {"stage": "update", "message": f"Downloading v{version} — {pct}%"})
                    self._event_bus.publish("pipeline", {"stage": "update", "message": f"Downloading v{version}..."})
                    download_and_apply(update, progress=_progress)
                else:
                    self._event_bus.publish("pipeline", {"stage": "update", "message": f"You're on the latest version (v{current_version()})"})
                    import time
                    time.sleep(3)
                    self._event_bus.publish("pipeline", {"stage": "done", "message": ""})
            except ConnectionError:
                self._event_bus.publish("pipeline", {"stage": "update", "message": "Couldn't check for updates — no internet or server unreachable"})
                import time
                time.sleep(4)
                self._event_bus.publish("pipeline", {"stage": "done", "message": ""})
            except Exception as e:
                self._event_bus.publish("app_error", {"message": f"Update check failed: {e}"})

        self._window.open()
        threading.Thread(target=_run, daemon=True).start()

    # ── Ollama lifecycle ──────────────────────────────────────────────────────

    def _start_ollama_async(self):
        def _run():
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
        try:
            ollama.stop_server()
            print("[ollama] stopped", flush=True)
        except Exception as e:
            print(f"[ollama] shutdown warning: {e}", flush=True)



# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    store.init()

    if not is_setup_complete():
        run_wizard()



    AnduinApp().run()


if __name__ == "__main__":
    main()
