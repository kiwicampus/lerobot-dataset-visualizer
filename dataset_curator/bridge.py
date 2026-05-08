"""Local HTTP bridge: visualizer syncs episode index; curator requests next episode after save."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from PyQt6.QtCore import QObject, pyqtSignal

DEFAULT_BRIDGE_PORT = 18765


class CuratorBridge(QObject):
    """Thread-safe flags + Qt signal to apply episode index on the UI thread."""

    episode_from_viz = pyqtSignal(int)
    language_instruction_from_viz = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._advance_pending = False

    def request_advance_episode(self) -> None:
        with self._lock:
            self._advance_pending = True

    def poll_advance(self) -> bool:
        with self._lock:
            if self._advance_pending:
                self._advance_pending = False
                return True
            return False


def _send_cors(handler: BaseHTTPRequestHandler) -> None:
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")


def run_bridge_server(
    bridge: CuratorBridge,
    port: int = DEFAULT_BRIDGE_PORT,
) -> ThreadingHTTPServer:
    bridge_ref = bridge

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *args) -> None:
            return

        def do_OPTIONS(self) -> None:
            self.send_response(204)
            _send_cors(self)
            self.end_headers()

        def do_POST(self) -> None:
            if self.path != "/sync":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw.decode("utf-8"))
                episode_id = int(body["episodeId"])
            except (ValueError, KeyError, json.JSONDecodeError):
                self.send_response(400)
                _send_cors(self)
                self.end_headers()
                return
            instruction = body.get("languageInstruction")
            if instruction is None:
                instruction_str = ""
            else:
                instruction_str = str(instruction)
            bridge_ref.episode_from_viz.emit(episode_id)
            bridge_ref.language_instruction_from_viz.emit(instruction_str)
            self.send_response(204)
            _send_cors(self)
            self.end_headers()

        def do_GET(self) -> None:
            if self.path != "/poll" and not self.path.startswith("/poll?"):
                self.send_error(404)
                return
            advance = bridge_ref.poll_advance()
            payload = json.dumps({"advance": advance}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            _send_cors(self)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server
