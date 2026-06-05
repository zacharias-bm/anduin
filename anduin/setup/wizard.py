from __future__ import annotations
"""First-launch setup wizard.

Serves wizard.html via a temporary HTTP server and displays it in
a native WKWebView window. All download/permission logic runs on
the server side with progress polling from the JS frontend.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from anduin.hardware.detect import detect as detect_hardware
from anduin.setup import models, ollama

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


# ── Shared progress state ────────────────────────────────────────────────────

_whisper_progress = {"downloaded": 0, "total": 0, "filename": "", "done": False, "error": ""}
_ollama_progress = {"done": 0, "total": 0, "complete": False, "error": ""}
_wizard_done = threading.Event()


# ── Wizard HTTP handler ─────────────────────────────────────────────────────

class _WizardHandler(BaseHTTPRequestHandler):
    hw = None

    def log_message(self, *args):
        pass

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length)

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/")

        if path in ("", "/", "/wizard.html"):
            self._serve_file("wizard.html")

        elif path == "/api/wizard/hardware":
            self._json(self.hw)

        elif path == "/api/wizard/whisper/status":
            model = self.hw["whisper_model"]
            self._json({"downloaded": models.whisper_is_downloaded(model)})

        elif path == "/api/wizard/whisper/progress":
            self._json(_whisper_progress)

        elif path == "/api/wizard/ollama/status":
            model = self.hw["llm_model"]
            installed = ollama.is_installed()
            running = ollama.is_running() if installed else False
            self._json({
                "installed": installed,
                "running": running,
                "model_pulled": ollama.model_is_pulled(model) if running else False,
            })

        elif path == "/api/wizard/ollama/progress":
            self._json(_ollama_progress)

        else:
            self._serve_file(path.lstrip("/"))

    def do_POST(self):
        path = self.path.split("?")[0].rstrip("/")

        if path == "/api/wizard/whisper/download":
            self._json({"ok": True})
            threading.Thread(target=self._download_whisper, daemon=True).start()

        elif path == "/api/wizard/ollama/pull":
            self._json({"ok": True})
            threading.Thread(target=self._pull_ollama, daemon=True).start()

        elif path == "/api/wizard/ollama/open-download":
            import subprocess
            subprocess.Popen(["open", "https://ollama.com/download/mac"])
            self._json({"ok": True})

        elif path == "/api/wizard/ollama/launch":
            import subprocess
            subprocess.Popen(["open", "-a", "Ollama"])
            self._json({"ok": True})

        elif path == "/api/wizard/focus":
            try:
                import AppKit
                app = AppKit.NSApplication.sharedApplication()
                app.activateIgnoringOtherApps_(True)
            except Exception:
                pass
            self._json({"ok": True})

        elif path == "/api/wizard/permission":
            granted = self._request_permission()
            self._json({"granted": granted})

        elif path == "/api/wizard/done":
            self._json({"ok": True})
            _wizard_done.set()

        else:
            self._json({"error": "not found"}, 404)

    def _serve_file(self, filename: str):
        file_path = WEB_DIR / filename
        if not file_path.is_file():
            self.send_error(404)
            return
        ct_map = {
            ".html": "text/html", ".css": "text/css",
            ".js": "application/javascript", ".svg": "image/svg+xml",
        }
        ct = ct_map.get(file_path.suffix, "application/octet-stream")
        body = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _download_whisper(self):
        global _whisper_progress
        _whisper_progress = {"downloaded": 0, "total": 0, "filename": "", "done": False, "error": ""}
        model = self.hw["whisper_model"]
        try:
            def _progress(downloaded, total, filename):
                _whisper_progress["downloaded"] = downloaded
                _whisper_progress["total"] = total
                _whisper_progress["filename"] = filename

            models.download_whisper_with_progress(model, progress=_progress)
            _whisper_progress["done"] = True
        except Exception as e:
            _whisper_progress["error"] = str(e)

    def _pull_ollama(self):
        global _ollama_progress
        _ollama_progress = {"done": 0, "total": 0, "complete": False, "error": ""}
        model = self.hw["llm_model"]
        try:
            ollama.ensure_running()

            def _prog(done_bytes, total_bytes):
                _ollama_progress["done"] = done_bytes
                _ollama_progress["total"] = total_bytes

            ollama.pull_model(model, progress=_prog)
            _ollama_progress["complete"] = True
        except Exception as e:
            _ollama_progress["error"] = str(e)

    def _request_permission(self):
        try:
            from anduin.capture.system_audio import request_permission
            return request_permission()
        except Exception:
            return False


# ── Public API ────────────────────────────────────────────────────────────────

def run_wizard():
    """Show the setup wizard in a native window. Blocks until done."""
    hw = detect_hardware()
    _WizardHandler.hw = hw

    # Start temporary server
    server = ThreadingHTTPServer(("127.0.0.1", 0), _WizardHandler)
    port = server.server_address[1]
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    # Open native window with WKWebView
    import AppKit
    import WebKit

    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)

    frame = AppKit.NSMakeRect(0, 0, 480, 420)
    style = (
        AppKit.NSTitledWindowMask
        | AppKit.NSClosableWindowMask
    )
    window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        frame, style, AppKit.NSBackingStoreBuffered, False
    )
    window.setTitle_("Anduin Setup")
    window.setReleasedWhenClosed_(False)
    window.setTitlebarAppearsTransparent_(True)
    window.setTitleVisibility_(1)  # hidden
    window.center()

    # Set background color to match wizard
    bg_color = AppKit.NSColor.colorWithRed_green_blue_alpha_(
        0.965, 0.957, 0.933, 1.0  # #F6F4EE
    )
    window.setBackgroundColor_(bg_color)

    config = WebKit.WKWebViewConfiguration.alloc().init()
    webview = WebKit.WKWebView.alloc().initWithFrame_configuration_(
        window.contentView().bounds(), config
    )
    webview.setAutoresizingMask_(
        AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable
    )
    # Make webview background transparent to show window bg
    webview.setValue_forKey_(False, "drawsBackground")
    window.contentView().addSubview_(webview)

    url = AppKit.NSURL.URLWithString_(f"http://127.0.0.1:{port}/wizard.html")
    webview.loadRequest_(AppKit.NSURLRequest.requestWithURL_(url))

    window.makeKeyAndOrderFront_(None)
    app.activateIgnoringOtherApps_(True)

    # Run the event loop until the wizard signals done or window closes
    while not _wizard_done.is_set():
        event = app.nextEventMatchingMask_untilDate_inMode_dequeue_(
            AppKit.NSEventMaskAny,
            AppKit.NSDate.dateWithTimeIntervalSinceNow_(0.1),
            AppKit.NSDefaultRunLoopMode,
            True,
        )
        if event:
            app.sendEvent_(event)
        if not window.isVisible():
            break

    window.orderOut_(None)
    server.shutdown()


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
