from __future__ import annotations
"""Main entry point. Run with: python -m anduin.ui.menubar"""
import queue
import subprocess
import threading
from datetime import datetime
from pathlib import Path

import AppKit
import objc
import rumps

from anduin.capture.devices import device_for_mode
from anduin.capture.recorder import Recorder
from anduin.hardware.detect import detect as detect_hardware
from anduin.pipeline import run as run_pipeline
from anduin.session import RecordingSession, recover_chunks
from anduin.setup import ollama
from anduin.setup.wizard import is_setup_complete, run_wizard
from anduin.storage import store
from anduin.ui.server import EventBus, start_server
from anduin.ui.webview import AnduinWindow


class _MenuTarget(AppKit.NSObject):
    """Bridge between NSMenu item actions and Python callbacks."""

    def initWithCallbacks_(self, callbacks):
        self = objc.super(_MenuTarget, self).init()
        if self is None:
            return None
        self._callbacks = callbacks
        return self

    @objc.typedSelector(b"v@:@")
    def menuAction_(self, sender):
        tag = sender.tag()
        cb = self._callbacks.get(tag)
        if cb:
            cb(sender)


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
        self._recovery_done = threading.Event()

        # UI Server & Window
        self._event_bus = EventBus()
        self._server, self._port = start_server(self._event_bus)
        self._server._app = self  # Link app to server for API calls
        self._window = AnduinWindow(self._port)
        
        self._build_menu()
        self._build_native_menu()
        self._start_ollama_async()
        self._recover_orphaned_recording()

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

    def _build_native_menu(self):
        """Build the macOS native application menu (top-left "Anduin" menu)."""
        callbacks = {
            1: self._open_window,
            2: self._record,
            3: self._stop_recording,
            4: self._open_settings,
            5: self._check_updates,
            6: self._quit,
        }
        self._menu_target = _MenuTarget.alloc().initWithCallbacks_(callbacks)
        action = b"menuAction:"

        menu_bar = AppKit.NSMenu.alloc().init()

        # "Anduin" app menu
        app_menu = AppKit.NSMenu.alloc().initWithTitle_("Anduin")

        item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Open Anduin", action, "o")
        item.setTarget_(self._menu_target)
        item.setTag_(1)
        app_menu.addItem_(item)

        app_menu.addItem_(AppKit.NSMenuItem.separatorItem())

        item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Record Meeting…", action, "r")
        item.setTarget_(self._menu_target)
        item.setTag_(2)
        app_menu.addItem_(item)
        self._native_record_item = item

        item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Stop Recording", action, ".")
        item.setTarget_(self._menu_target)
        item.setTag_(3)
        item.setEnabled_(False)
        app_menu.addItem_(item)
        self._native_stop_item = item

        app_menu.addItem_(AppKit.NSMenuItem.separatorItem())

        item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Settings…", action, ",")
        item.setTarget_(self._menu_target)
        item.setTag_(4)
        app_menu.addItem_(item)

        item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Check for Updates…", action, "")
        item.setTarget_(self._menu_target)
        item.setTag_(5)
        app_menu.addItem_(item)

        app_menu.addItem_(AppKit.NSMenuItem.separatorItem())

        item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Quit Anduin", action, "q")
        item.setTarget_(self._menu_target)
        item.setTag_(6)
        app_menu.addItem_(item)

        # Wrap app_menu under a top-level item
        top_item = AppKit.NSMenuItem.alloc().init()
        top_item.setSubmenu_(app_menu)
        menu_bar.addItem_(top_item)

        AppKit.NSApp.setMainMenu_(menu_bar)

    def _sync_native_menu_state(self, recording: bool):
        """Enable/disable native menu items to match recording state."""
        if hasattr(self, "_native_record_item"):
            self._native_record_item.setEnabled_(not recording)
        if hasattr(self, "_native_stop_item"):
            self._native_stop_item.setEnabled_(recording)

    def _quit(self, _):
        import os

        # Hide the window immediately so it feels instant
        if hasattr(self, "_window"):
            self._window.close()

        # CRITICAL: If recording is active, flush the current chunk SYNCHRONOUSLY
        # before exiting. Chunk files already on disk are safe; we just need
        # to save whatever's accumulated since the last flush.
        if self._recorder.is_recording:
            print("[quit] recording active — flushing audio before exit...", flush=True)
            try:
                session = getattr(self, "_session", None)
                if session:
                    # Stop the audio stream first so no new data arrives during flush
                    self._recorder.stop_stream()
                    session._stop_event.set()
                    session._flush_chunk()
                    print("[quit] final chunk flushed to _active_recording/", flush=True)
                    # Leave _active_recording/ on disk — recover_chunks() picks it up
                else:
                    # Fallback: save the whole recording as a single file
                    tmp_path = getattr(self, "_tmp_audio", store.APP_DIR / "recording_tmp.wav")
                    self._recorder.stop(tmp_path)
                    print(f"[quit] audio saved to {tmp_path}", flush=True)
            except Exception as e:
                print(f"[quit] WARNING: could not save recording: {e}", flush=True)

        # Fire remaining cleanup in daemon threads so we don't block
        def _cleanup():
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
        # Don't start a new recording while crash recovery is processing chunks
        if not self._recovery_done.is_set():
            self._event_bus.publish("app_error", {"message": "Recovering previous recording — please wait a moment."})
            return
        # Keep tmp_audio as a fallback path for quit-during-recording
        self._tmp_audio = store.APP_DIR / "recording_tmp.wav"
        if mode == "digital":
            self._recorder.start(mode="digital")
        else:
            self._recorder.start(device=device_for_mode(mode), mode="inperson")

        # Start chunked recording session (handles flush timer + background workers)
        def _session_progress(stage: str, msg: str):
            print(f"[session] {stage}: {msg}", flush=True)
            self._event_bus.publish("pipeline", {"stage": stage, "message": msg})

        self._session = RecordingSession(
            recorder=self._recorder,
            on_progress=_session_progress,
        )
        self._session.start()

        self._pulse_timer.start()
        self.title = "Recording"
        self.menu["Record Meeting…"].set_callback(None)
        self.menu["Stop Recording"].set_callback(self._stop_recording)
        self._sync_native_menu_state(True)
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
        self._sync_native_menu_state(False)
        self._event_bus.publish("recording", {"active": False})
        self._event_bus.publish("pipeline", {"stage": "saving", "message": "Transcribing..."})
        self._server._app_status = {"recording": False, "pipeline_stage": "saving"}

        def _finish():
            try:
                title = datetime.now().strftime("%Y-%m-%d %H:%M")
                session = getattr(self, "_session", None)
                if session:
                    out_dir = session.finish(title)
                    self._session = None
                    # Clean up any leftover recording_tmp.wav
                    self._cleanup_tmp_recording()
                else:
                    # Fallback: legacy non-chunked path
                    tmp_path = self._recorder.stop(self._tmp_audio)
                    self._run_pipeline_async(tmp_path, title)
            except Exception as e:
                import traceback
                print(f"[recorder] stop/finish failed: {e}", flush=True)
                traceback.print_exc()
                self._session = None
                self._recorder.is_recording = False
                self._event_bus.publish("pipeline", {"stage": "done", "message": ""})
                self._event_bus.publish("app_error", {"message": f"Recording failed: {e}"})

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

                auto_summarize = store.get_config("auto_summarize", True)

                out_dir = run_pipeline(
                    audio_path=audio_path,
                    title=title,
                    auto_summarize=auto_summarize,
                    progress=_progress,
                )
                self.title = None # Keep logo only

                # Pipeline succeeded (audio is safely in meeting dir).
                # Clean up temp recording file.
                self._cleanup_tmp_recording()

            except Exception as e:
                import traceback
                print(f"[pipeline] ERROR: {e}", flush=True)
                traceback.print_exc()
                self.title = None # Keep logo only
                self._event_bus.publish("app_error", {"message": str(e)})
                # Check if the audio was already copied to the meeting dir.
                # If so, we can safely remove the tmp file.
                # If not, leave it for crash recovery on next launch.
                self._cleanup_tmp_recording_if_safe()

        threading.Thread(target=_run, daemon=True).start()

    def _cleanup_tmp_recording(self):
        """Remove the temp recording file after the pipeline has secured the audio."""
        tmp = store.APP_DIR / "recording_tmp.wav"
        if tmp.exists():
            try:
                tmp.unlink()
                print("[pipeline] temp recording cleaned up", flush=True)
            except Exception:
                pass

    def _cleanup_tmp_recording_if_safe(self):
        """Remove temp recording only if a meeting dir already has the audio."""
        tmp = store.APP_DIR / "recording_tmp.wav"
        if not tmp.exists():
            return
        # Check if any recent meeting dir has an audio.wav (meaning copy succeeded)
        try:
            recent = sorted(store.MEETINGS_DIR.iterdir(), reverse=True)[:1]
            if recent and (recent[0] / "audio.wav").exists():
                tmp.unlink()
                print("[pipeline] temp recording cleaned up (audio safe in meeting dir)", flush=True)
            else:
                print("[pipeline] keeping temp recording for crash recovery", flush=True)
        except Exception:
            pass  # When in doubt, keep the file

    # ── Settings ──────────────────────────────────────────────────────────────

    @rumps.clicked("Settings…")
    def _open_settings(self, _):
        # Open the app window and navigate to the settings panel
        self._window.open()
        self._window.evaluate_js("showSettings()")

    # ── Updates ───────────────────────────────────────────────────────────────

    def _check_updates_async(self):
        """Background update check on launch — auto-downloads if available."""
        def _run():
            import time
            time.sleep(5)
            try:
                from anduin.updater import check_for_update, cleanup_old_backup
                cleanup_old_backup()
                update = check_for_update()
                if update:
                    self._apply_update(update)
            except Exception as e:
                print(f"[updater] background check failed: {e}", flush=True)

        threading.Thread(target=_run, daemon=True).start()

    def _check_updates(self, _):
        """Manual update check from the menu."""
        def _run():
            try:
                from anduin.updater import check_for_update, current_version
                self._event_bus.publish("pipeline", {"stage": "update", "message": "Checking for updates..."})
                update = check_for_update()
                if update:
                    self._apply_update(update)
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

    def _apply_update(self, update):
        from anduin.updater import download_and_apply
        version = update["version"]

        def _progress(downloaded, total):
            if total > 0:
                pct = round(downloaded / total * 100)
                self._event_bus.publish("pipeline", {"stage": "update", "message": f"Downloading v{version} — {pct}%"})

        def _on_stage(msg):
            self._event_bus.publish("pipeline", {"stage": "update", "message": msg})

        self._window.open()
        self._event_bus.publish("pipeline", {"stage": "update", "message": f"Downloading v{version}..."})
        success = download_and_apply(update, progress=_progress, on_stage=_on_stage)
        if not success:
            self._event_bus.publish("pipeline", {"stage": "update", "message": "Update failed — try again later"})
            import time
            time.sleep(4)
            self._event_bus.publish("pipeline", {"stage": "done", "message": ""})

    def _recover_orphaned_recording(self):
        """Check for audio left over from a crash or quit-during-recording.

        Handles two cases:
          1. _active_recording/ dir with chunk files (new chunked sessions)
          2. recording_tmp.wav (legacy single-file recordings)
        """
        # Quick check on main thread: if there's nothing to recover,
        # unblock recording immediately instead of waiting for the background thread.
        chunk_dir = store.APP_DIR / "_active_recording"
        tmp = store.APP_DIR / "recording_tmp.wav"
        has_chunks = chunk_dir.exists() and any(chunk_dir.glob("chunk_*.wav"))
        has_tmp = tmp.exists() and tmp.stat().st_size >= 1024

        if not has_chunks and not has_tmp:
            self._recovery_done.set()
            return

        def _run():
            import time
            time.sleep(3)  # Let the UI finish loading

            try:
                def _progress(stage, msg):
                    self._event_bus.publish("pipeline", {"stage": stage, "message": msg})

                # Case 1: Chunked session recovery
                if has_chunks:
                    try:
                        result = recover_chunks(on_progress=_progress)
                        if result:
                            self._event_bus.publish("pipeline", {"stage": "done", "message": ""})
                            return
                    except Exception as e:
                        print(f"[recovery] chunk recovery failed: {e}", flush=True)

                # Case 2: Legacy recording_tmp.wav
                if not has_tmp:
                    return

                print(f"[recovery] found orphaned recording: {tmp}", flush=True)
                try:
                    from datetime import datetime as _dt
                    mtime = tmp.stat().st_mtime
                    title = _dt.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M") + " (recovered)"
                    _progress("recover", "Recovering recording from last session...")
                    self._run_pipeline_async(tmp, title)
                except Exception as e:
                    print(f"[recovery] failed: {e}", flush=True)
            finally:
                self._recovery_done.set()

        threading.Thread(target=_run, daemon=True).start()

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
