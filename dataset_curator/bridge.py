"""Local HTTP bridge: visualizer syncs episode index; curator requests next episode after save."""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from queue import Empty, Queue

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

DEFAULT_BRIDGE_PORT = 18765


def _dbg(msg: str) -> None:
    """Set CURATOR_BRIDGE_DEBUG=0 to silence."""
    if os.environ.get("CURATOR_BRIDGE_DEBUG", "1").strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    ):
        return
    print(f"[CuratorBridge] {msg}", flush=True)


def curator_debug(msg: str) -> None:
    """Same toggling as bridge logs; use from PyQt UI slots."""
    _dbg(msg)


class CuratorBridge(QObject):
    """Thread-safe flags + Qt signal to apply episode index on the UI thread."""

    episode_from_viz = pyqtSignal(int)
    language_instruction_from_viz = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._advance_pending = False
        self._resync_from_viz_pending = False
        self._navigate_to_pending: int | None = None
        self._sync_queue: Queue = Queue()
        # HTTP workers run in background threads; QMetaObject.invokeMethod is flaky for slots in PyQt6.
        # Drain pending /sync payloads on the GUI thread every tick (bulletproof).
        self._sync_timer = QTimer(self)
        self._sync_timer.setInterval(50)
        self._sync_timer.timeout.connect(self._drain_sync_queue_on_gui_thread)
        self._sync_timer.start()

    def _drain_sync_queue_on_gui_thread(self) -> None:
        while True:
            try:
                episode_id, instruction = self._sync_queue.get_nowait()
            except Empty:
                break
            _dbg(
                f"GUI drain → emit episode_id={episode_id} "
                f"instruction_len={len(instruction)} queue_left≈{self._sync_queue.qsize()}"
            )
            self.episode_from_viz.emit(episode_id)
            self.language_instruction_from_viz.emit(instruction)

    def request_advance_episode(self) -> None:
        with self._lock:
            self._advance_pending = True

    def request_resync_from_visualizer(self) -> None:
        """Curator UI asks the browser to POST /sync again on next /poll (e.g. after reopen)."""
        with self._lock:
            self._resync_from_viz_pending = True

    def request_navigate_to(self, episode_id: int) -> None:
        """Tell the visualizer to navigate to a specific episode on the next /poll."""
        with self._lock:
            self._navigate_to_pending = episode_id

    def poll_advance_and_resync(self) -> tuple[bool, bool, int | None]:
        """One-shot read for advance, resync, and navigateTo flags."""
        with self._lock:
            advance = self._advance_pending
            if advance:
                self._advance_pending = False
            resync = self._resync_from_viz_pending
            if resync:
                self._resync_from_viz_pending = False
            navigate_to = self._navigate_to_pending
            self._navigate_to_pending = None
            return advance, resync, navigate_to


def _enqueue_sync(bridge: CuratorBridge, episode_id: int, instruction_str: str) -> None:
    bridge._sync_queue.put((episode_id, instruction_str))
    _dbg(
        f"enqueue sync episode_id={episode_id} "
        f"instruction_len={len(instruction_str)} "
        f"thread={threading.current_thread().name} "
        f"queue_size={bridge._sync_queue.qsize()}"
    )


def _send_cors(handler: BaseHTTPRequestHandler) -> None:
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")


def run_bridge_server(
    bridge: CuratorBridge,
    port: int = DEFAULT_BRIDGE_PORT,
    host: str = "0.0.0.0",
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
            except (ValueError, KeyError, json.JSONDecodeError) as e:
                _dbg(f"HTTP POST /sync BAD JSON or episodeId: {e!r} raw_len={len(raw)}")
                self.send_response(400)
                _send_cors(self)
                self.end_headers()
                return
            instruction = body.get("languageInstruction")
            if instruction is None:
                instruction_str = ""
            else:
                instruction_str = str(instruction)
            _dbg(f"HTTP POST /sync client={self.client_address[0]} body_ok episode_id={episode_id}")
            _enqueue_sync(bridge_ref, episode_id, instruction_str)
            self.send_response(204)
            _send_cors(self)
            self.end_headers()

        def do_GET(self) -> None:
            if self.path == "/request-resync" or self.path.startswith(
                "/request-resync?"
            ):
                bridge_ref.request_resync_from_visualizer()
                _dbg("HTTP GET /request-resync → pending re-push from visualizer on next /poll")
                self.send_response(204)
                _send_cors(self)
                self.end_headers()
                return
            if self.path != "/poll" and not self.path.startswith("/poll?"):
                self.send_error(404)
                return
            advance, resync, navigate_to = bridge_ref.poll_advance_and_resync()
            if advance:
                _dbg("HTTP GET /poll → advance=True")
            if resync:
                _dbg("HTTP GET /poll → resync=True")
            if navigate_to is not None:
                _dbg(f"HTTP GET /poll → navigateTo={navigate_to}")
            payload = json.dumps({"advance": advance, "resync": resync, "navigateTo": navigate_to}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            _send_cors(self)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer((host, port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _dbg(f"HTTP server listening on http://{host}:{port} (sync POST /sync poll GET /poll request-resync GET /request-resync)")
    return server
