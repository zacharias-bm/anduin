from __future__ import annotations
import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from anduin.pipeline import summarize_meeting
from anduin.storage import store

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


class EventBus:
    def __init__(self):
        self._subscribers: list[queue.Queue] = []
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue):
        with self._lock:
            self._subscribers = [s for s in self._subscribers if s is not q]

    def publish(self, event: str, data: dict):
        msg = f"event: {event}\ndata: {json.dumps(data)}\n\n"
        with self._lock:
            for q in self._subscribers:
                q.put(msg)


class _Handler(BaseHTTPRequestHandler):
    event_bus: EventBus

    def log_message(self, format, *args):
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
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/meetings":
            meetings = store.list_meetings(limit=100)
            self._json(meetings)

        elif path.startswith("/api/meetings/search"):
            qs = parse_qs(parsed.query)
            q = qs.get("q", [""])[0]
            self._json(store.search(q) if q else [])

        elif path.startswith("/api/meetings/") and path.count("/") == 3:
            try:
                mid = int(path.split("/")[3])
            except ValueError:
                self._json({"error": "invalid id"}, 400)
                return
            meeting = store.get_meeting(mid)
            if meeting:
                self._json(meeting)
            else:
                self._json({"error": "not found"}, 404)

        elif path == "/api/status":
            self._json(getattr(self.server, "_app_status", {"recording": False, "pipeline_stage": None}))

        elif path == "/api/settings":
            self._json({
                "auto_summarize": store.get_config("auto_summarize", True),
                "keep_audio": store.get_config("keep_audio", False),
                "manage_ollama": store.get_config("manage_ollama", True),
            })

        elif path == "/api/speakers":
            self._json(store.get_speaker_names())

        elif path == "/api/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            q = self.event_bus.subscribe()
            try:
                while True:
                    try:
                        msg = q.get(timeout=30)
                        self.wfile.write(msg.encode())
                        self.wfile.flush()
                    except queue.Empty:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                self.event_bus.unsubscribe(q)

        else:
            self._serve_static(parsed.path)

    def do_PUT(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path.startswith("/api/meetings/") and path.endswith("/title"):
            parts = path.split("/")
            try:
                mid = int(parts[3])
            except ValueError:
                self._json({"error": "invalid id"}, 400)
                return
            body = json.loads(self._read_body())
            store.update_title(mid, body.get("title", ""))
            self._json({"ok": True})

        elif path == "/api/settings":
            body = json.loads(self._read_body())
            for k, v in body.items():
                store.set_config(k, v)
            self._json({"ok": True})

        elif path == "/api/speakers":
            body = json.loads(self._read_body())
            # Expects {"speaker_id": "New Name", ...}
            for sid, name in body.items():
                store.set_speaker_name(sid, name)
            self._json({"ok": True})

        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path.startswith("/api/meetings/") and path.endswith("/summarize"):
            parts = path.split("/")
            try:
                mid = int(parts[3])
            except ValueError:
                self._json({"error": "invalid id"}, 400)
                return
            meeting = store.get_meeting(mid)
            if not meeting:
                self._json({"error": "not found"}, 404)
                return
            self._json({"status": "started"}, 202)

            def _run():
                try:
                    self.event_bus.publish("summarize_start", {"meeting_id": mid})
                    summarize_meeting(
                        Path(meeting["path"]),
                        progress=lambda stage, msg: self.event_bus.publish("pipeline", {"stage": stage, "message": msg}),
                    )
                    self.event_bus.publish("summarize_done", {"meeting_id": mid})
                except Exception as e:
                    self.event_bus.publish("error", {"message": str(e)})

            threading.Thread(target=_run, daemon=True).start()

        elif path.startswith("/api/meetings/") and path.endswith("/rename_speaker"):
            parts = path.split("/")
            try:
                mid = int(parts[3])
            except ValueError:
                self._json({"error": "invalid id"}, 400)
                return
            body = json.loads(self._read_body())
            old_name = body.get("old_name")
            new_name = body.get("new_name")
            if not old_name or not new_name:
                self._json({"error": "missing name"}, 400)
                return

            store.rename_speaker(mid, old_name, new_name)
            self._json({"ok": True})

        elif path == "/api/record":
            if hasattr(self.server, "_app"):
                body = {}
                try:
                    body = json.loads(self._read_body())
                except Exception:
                    pass
                mode = body.get("mode", "inperson")
                self.server._app._cmd_queue.put(("record", mode))
                self._json({"ok": True})
            else:
                self._json({"error": "app not linked"}, 500)
        elif path == "/api/stop":
            if hasattr(self.server, "_app"):
                self.server._app._cmd_queue.put(("stop", None))
                self._json({"ok": True})
            else:
                self._json({"error": "app not linked"}, 500)
        else:
            self._json({"error": "not found"}, 404)

    def _serve_static(self, url_path: str):
        if url_path in ("", "/"):
            url_path = "/index.html"
        file_path = WEB_DIR / url_path.lstrip("/")
        if not file_path.is_file():
            self.send_error(404)
            return

        content_types = {
            ".html": "text/html",
            ".css": "text/css",
            ".js": "application/javascript",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".ico": "image/x-icon",
        }
        ct = content_types.get(file_path.suffix, "application/octet-stream")
        body = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_server(event_bus: EventBus) -> tuple[ThreadingHTTPServer, int]:
    _Handler.event_bus = event_bus
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server._app_status = {"recording": False, "pipeline_stage": None}
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port
