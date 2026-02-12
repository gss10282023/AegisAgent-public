from __future__ import annotations

import hashlib
import io
import json
import threading
import time
from dataclasses import dataclass
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import BaseHandler, Request, build_opener, install_opener
from urllib.response import addinfourl

_INPROC_LOCK = threading.Lock()
_INPROC_REGISTRY: dict[tuple[str, int], "HttpJsonActionRecorder"] = {}
_INPROC_PORT_NEXT = 48000
_INPROC_PREV_OPENER = None
_INPROC_INSTALLED = False


def _alloc_inproc_port() -> int:
    global _INPROC_PORT_NEXT
    with _INPROC_LOCK:
        port = _INPROC_PORT_NEXT
        _INPROC_PORT_NEXT += 1
        return port


class _InprocHttpHandler(BaseHandler):
    handler_order = 0

    def http_open(self, req: Request) -> Any:  # noqa: ANN401
        url = req.full_url
        parts = urlsplit(url)
        host = parts.hostname or ""
        port = int(parts.port) if parts.port is not None else 80
        key = (host, port)
        with _INPROC_LOCK:
            recorder = _INPROC_REGISTRY.get(key)

        if recorder is None:
            return None

        method = req.get_method()
        body = bytes(req.data) if req.data else b""
        status, payload_obj = recorder._handle_inproc_request(
            method=method, endpoint=parts.path, body=body
        )
        data = _json_dumps_canonical(payload_obj).encode("utf-8")

        headers = Message()
        headers["Content-Type"] = "application/json; charset=utf-8"
        headers["Content-Length"] = str(len(data))

        if int(status) >= 400:
            raise HTTPError(url, int(status), "inproc_error", headers, io.BytesIO(data))

        resp = addinfourl(io.BytesIO(data), headers, url, code=int(status))
        resp.msg = "OK"
        return resp


def _ensure_inproc_opener_installed() -> None:
    global _INPROC_INSTALLED, _INPROC_PREV_OPENER
    if _INPROC_INSTALLED:
        return

    try:
        import urllib.request as _urllib_request

        _INPROC_PREV_OPENER = getattr(_urllib_request, "_opener", None)
    except Exception:
        _INPROC_PREV_OPENER = None

    opener = build_opener(_InprocHttpHandler())
    install_opener(opener)
    _INPROC_INSTALLED = True


def _maybe_restore_inproc_opener() -> None:
    global _INPROC_INSTALLED, _INPROC_PREV_OPENER
    if not _INPROC_INSTALLED:
        return
    with _INPROC_LOCK:
        if _INPROC_REGISTRY:
            return

    try:
        import urllib.request as _urllib_request

        prev = _INPROC_PREV_OPENER
        if prev is not None:
            install_opener(prev)
        else:
            setattr(_urllib_request, "_opener", None)
    except Exception:
        pass
    finally:
        _INPROC_INSTALLED = False
        _INPROC_PREV_OPENER = None


def _utc_ms() -> int:
    return int(time.time() * 1000)


def _json_dumps_canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_prefixed(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


@dataclass(frozen=True)
class HttpJsonActionRecorderConfig:
    host: str = "127.0.0.1"
    port: int = 0
    act_path: str = "/act"
    health_path: str = "/health"


class HttpJsonActionRecorder:
    """Minimal HTTP JSON action recorder (Phase3 3b-5b).

    Provides:
      - `POST /act` accepts a JSON object (must include non-empty 'type').
      - Records request/response JSONL lines to comm_proxy_trace.jsonl.
    """

    def __init__(
        self, trace_path: Path, *, config: HttpJsonActionRecorderConfig | None = None
    ) -> None:
        self.trace_path = Path(trace_path)
        self.config = config or HttpJsonActionRecorderConfig()

        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._server: ThreadingHTTPServer | None = None
        self._trace_f = None
        self._next_action_id = 1
        self._inproc_address: tuple[str, int] | None = None

    @property
    def address(self) -> tuple[str, int]:
        if self._inproc_address is not None:
            return self._inproc_address
        server = self._server
        if server is None:
            return (self.config.host, int(self.config.port))
        host, port = server.server_address[:2]
        return (str(host), int(port))

    @property
    def base_url(self) -> str:
        host, port = self.address
        return f"http://{host}:{port}"

    def start(self) -> None:
        if self._server is not None:
            return
        if self._inproc_address is not None:
            return

        self.trace_path.parent.mkdir(parents=True, exist_ok=True)
        self._trace_f = self.trace_path.open("a", encoding="utf-8")

        recorder = self

        class _Handler(BaseHTTPRequestHandler):
            server: ThreadingHTTPServer  # type: ignore[assignment]

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return

            def _read_body(self) -> bytes:
                try:
                    n = int(self.headers.get("Content-Length") or "0")
                except Exception:
                    n = 0
                if n <= 0:
                    return b""
                return self.rfile.read(n)

            def _send_json(self, status: int, obj: Any) -> None:
                data = _json_dumps_canonical(obj).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _record_request(self, endpoint: str, body: bytes) -> tuple[str | None, Any | None]:
                payload: Any | None = None
                payload_digest: str | None = None

                if body:
                    try:
                        payload = json.loads(body.decode("utf-8"))
                    except Exception:
                        payload_digest = _sha256_prefixed(body)
                else:
                    payload = {}

                recorder._write_trace_line(
                    {
                        "timestamp_ms": _utc_ms(),
                        "direction": "request",
                        "endpoint": endpoint,
                        **(
                            {"payload": payload}
                            if payload_digest is None
                            else {"payload_digest": payload_digest}
                        ),
                    }
                )
                return payload_digest, payload

            def _record_response(self, endpoint: str, status: int, payload_obj: Any) -> None:
                recorder._write_trace_line(
                    {
                        "timestamp_ms": _utc_ms(),
                        "direction": "response",
                        "endpoint": endpoint,
                        "status": int(status),
                        "payload": payload_obj,
                    }
                )

            def do_GET(self) -> None:  # noqa: N802
                endpoint = urlsplit(self.path).path
                body = b""
                self._record_request(endpoint, body)

                if endpoint == recorder.config.health_path:
                    payload_obj = {"status": "ok"}
                    self._send_json(200, payload_obj)
                    self._record_response(endpoint, 200, payload_obj)
                    return

                payload_obj = {"ok": False, "error": "not_found"}
                self._send_json(404, payload_obj)
                self._record_response(endpoint, 404, payload_obj)

            def do_POST(self) -> None:  # noqa: N802
                endpoint = urlsplit(self.path).path
                body = self._read_body()
                _, payload = self._record_request(endpoint, body)

                if endpoint == recorder.config.act_path:
                    if (
                        not isinstance(payload, dict)
                        or not isinstance(payload.get("type"), str)
                        or not payload["type"].strip()
                    ):
                        payload_obj = {"ok": False, "error": "invalid_action"}
                        self._send_json(400, payload_obj)
                        self._record_response(endpoint, 400, payload_obj)
                        return

                    action_id = recorder._next_action_id_str()
                    payload_obj = {"ok": True, "action_id": action_id}
                    self._send_json(200, payload_obj)
                    self._record_response(endpoint, 200, payload_obj)
                    return

                payload_obj = {"ok": False, "error": "not_found"}
                self._send_json(404, payload_obj)
                self._record_response(endpoint, 404, payload_obj)

        try:
            self._server = ThreadingHTTPServer((self.config.host, int(self.config.port)), _Handler)
        except PermissionError:
            # Some sandboxed environments do not allow binding listen sockets. Fall back to an
            # in-process urllib handler that emulates the HTTP surface (enough for unit/integration
            # tests).
            _ensure_inproc_opener_installed()
            host = str(self.config.host)
            port = int(self.config.port)
            if port == 0:
                port = _alloc_inproc_port()
            self._inproc_address = (host, port)
            with _INPROC_LOCK:
                key = (host, port)
                if key in _INPROC_REGISTRY:
                    raise RuntimeError(f"inproc comm proxy address already registered: {key}")
                _INPROC_REGISTRY[key] = self
            return

        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._inproc_address is not None:
            with _INPROC_LOCK:
                _INPROC_REGISTRY.pop(self._inproc_address, None)
            self._inproc_address = None
            _maybe_restore_inproc_opener()

            f = self._trace_f
            self._trace_f = None
            if f is not None:
                try:
                    f.close()
                except Exception:
                    pass
            return

        server = self._server
        if server is None:
            return

        try:
            server.shutdown()
            server.server_close()
        finally:
            self._server = None

        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)
        self._thread = None

        f = self._trace_f
        self._trace_f = None
        if f is not None:
            try:
                f.close()
            except Exception:
                pass

    def __enter__(self) -> "HttpJsonActionRecorder":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        self.stop()

    def _next_action_id_str(self) -> str:
        with self._lock:
            action_id = self._next_action_id
            self._next_action_id += 1
        return f"a{action_id}"

    def _write_trace_line(self, obj: dict[str, Any]) -> None:
        f = self._trace_f
        if f is None:
            raise RuntimeError("recorder is not started")
        line = _json_dumps_canonical(obj)
        with self._lock:
            f.write(line)
            f.write("\n")
            f.flush()

    def _handle_inproc_request(self, *, method: str, endpoint: str, body: bytes) -> tuple[int, Any]:
        endpoint = urlsplit(endpoint).path
        method_norm = str(method or "").strip().upper()

        payload: Any | None = None
        payload_digest: str | None = None
        if method_norm == "GET":
            payload = {}
        else:
            if body:
                try:
                    payload = json.loads(body.decode("utf-8"))
                except Exception:
                    payload_digest = _sha256_prefixed(body)
            else:
                payload = {}

        self._write_trace_line(
            {
                "timestamp_ms": _utc_ms(),
                "direction": "request",
                "endpoint": endpoint,
                **(
                    {"payload": payload}
                    if payload_digest is None
                    else {"payload_digest": payload_digest}
                ),
            }
        )

        if method_norm == "GET" and endpoint == self.config.health_path:
            payload_obj = {"status": "ok"}
            self._write_trace_line(
                {
                    "timestamp_ms": _utc_ms(),
                    "direction": "response",
                    "endpoint": endpoint,
                    "status": 200,
                    "payload": payload_obj,
                }
            )
            return 200, payload_obj

        if method_norm == "POST" and endpoint == self.config.act_path:
            if (
                not isinstance(payload, dict)
                or not isinstance(payload.get("type"), str)
                or not payload["type"].strip()
            ):
                payload_obj = {"ok": False, "error": "invalid_action"}
                self._write_trace_line(
                    {
                        "timestamp_ms": _utc_ms(),
                        "direction": "response",
                        "endpoint": endpoint,
                        "status": 400,
                        "payload": payload_obj,
                    }
                )
                return 400, payload_obj

            action_id = self._next_action_id_str()
            payload_obj = {"ok": True, "action_id": action_id}
            self._write_trace_line(
                {
                    "timestamp_ms": _utc_ms(),
                    "direction": "response",
                    "endpoint": endpoint,
                    "status": 200,
                    "payload": payload_obj,
                }
            )
            return 200, payload_obj

        payload_obj = {"ok": False, "error": "not_found"}
        self._write_trace_line(
            {
                "timestamp_ms": _utc_ms(),
                "direction": "response",
                "endpoint": endpoint,
                "status": 404,
                "payload": payload_obj,
            }
        )
        return 404, payload_obj
