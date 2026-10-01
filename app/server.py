"""HTTP application: page, health check and the real calibration API."""

from __future__ import annotations

import json
import logging
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

from .alignment import solve, solve_shared
from .validation import ValidationError, validate_payload

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_BODY_BYTES = 64 * 1024

logger = logging.getLogger("coincidence")


class Handler(BaseHTTPRequestHandler):
    server_version = "CoincidenceCalibrator/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.info("%s - %s", self.address_string(), fmt % args)

    # -- helpers -----------------------------------------------------------
    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str) -> None:
        try:
            body = path.read_bytes()
        except OSError:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # -- routes ------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
        elif path == "/healthz":
            self._send_json(HTTPStatus.OK, {"status": "ok"})
        elif path == "/static/app.js":
            self._send_file(STATIC_DIR / "app.js", "application/javascript; charset=utf-8")
        elif path == "/static/styles.css":
            self._send_file(STATIC_DIR / "styles.css", "text/css; charset=utf-8")
        else:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "未找到资源"})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path != "/api/calibrate":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "未找到资源"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "请求头无效"})
            return
        if length <= 0 or length > MAX_BODY_BYTES:
            self._send_json(
                HTTPStatus.BAD_REQUEST, {"error": "请求体缺失或过大"}
            )
            return

        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "请求体不是合法 JSON"})
            return

        try:
            params = validate_payload(data)
        except ValidationError as exc:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": exc.message, "field": exc.field},
            )
            return

        if params.pop("shared"):
            result = solve_shared(
                params["A"],
                params["B"],
                params["A2"],
                params["B2"],
                params["offset_min"],
                params["offset_max"],
                params["tolerance"],
                params["min_pairs"],
            )
        else:
            result = solve(
                params["A"],
                params["B"],
                params["offset_min"],
                params["offset_max"],
                params["tolerance"],
                params["min_pairs"],
            )
        self._send_json(HTTPStatus.OK, result.to_dict())


def build_server(port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.daemon_threads = True
    return server


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    port = int(os.environ.get("APP_PORT", "8080"))
    server = build_server(port)
    logger.info("coincidence calibration service listening on port %d", port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
