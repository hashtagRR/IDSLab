# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
"""HTTP server of the dashboard (standard library only).

Local by design: it binds to 127.0.0.1 unless told otherwise, refuses requests whose Host header
is not its own (DNS rebinding), and takes POST only as same-origin JSON, which a page on another
site cannot send without a CORS preflight this server never approves.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from idslab import __version__
from idslab.cli import EXAMPLES, validation_text
from idslab.config import EXTRAS, MODES, OBJECTIVES, ConfigError, dump_yaml, from_dict, parse_yaml
from idslab.dashboard.jobs import COMMANDS, DEFAULT_OUT, Job, JobError, build_command
from idslab.dashboard.catalog import catalog
from idslab.dashboard.workspace import PathError, Workspace, describe, inspect_data
from idslab.runner import to_jsonable

logger = logging.getLogger(__name__)

MAX_BODY = 2_000_000
MAX_FILE = 50_000_000
STATIC = {"index.html", "styles.css", "app.js", "core.js", "charts.js", "views.js", "editor.js", "builder.js"}
PAGE_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "frame-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
# report.html is self-contained: inline styles and SVG, no scripts
REPORT_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; frame-ancestors 'self'"
TEXT_TYPES = {".md", ".json", ".yaml", ".yml", ".txt", ".log", ".csv"}


def _extras() -> dict[str, bool]:
    from importlib.util import find_spec  # noqa: PLC0415

    return {extra: find_spec(module) is not None for module, extra in EXTRAS.items()}


def _templates() -> dict[str, str]:
    return {
        name: (resources.files("idslab.examples") / name).read_text(encoding="utf-8") for name in EXAMPLES
    }


class App:
    def __init__(self, workspace: str):
        self.ws = Workspace(workspace)
        self.job = Job(self.ws.root)
        self._catalog: dict | None = None

    def catalog(self) -> dict:
        if self._catalog is None:  # imports every listed library once; cached for the session
            self._catalog = catalog()
        return self._catalog

    def meta(self) -> dict:
        return {
            "version": __version__,
            "workspace": self.ws.root,
            "examples": list(EXAMPLES),
            "commands": list(COMMANDS),
            "default_out": DEFAULT_OUT,
            "modes": list(MODES),
            "objectives": list(OBJECTIVES),
            "extras": _extras(),
            "cpu_count": os.cpu_count(),
        }

    def validate(self, text: str, rel: str | None) -> dict:
        """The loader ``idslab validate`` uses; relative data paths resolve against the config's folder."""
        if not text.strip():
            return {"ok": False, "error": "the config is empty"}
        path = self.ws.resolve(rel) if rel else None
        base = os.path.dirname(path) if path else self.ws.root
        try:
            cfg = from_dict(parse_yaml(text), base_dir=base)
        except (ConfigError, ImportError) as e:
            return {"ok": False, "error": str(e)}
        except ValueError as e:  # YAML syntax errors
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        return {
            "ok": True,
            "config": describe(cfg),
            "text": validation_text(rel or "(unsaved config)", cfg),
        }


class Handler(BaseHTTPRequestHandler):
    app: App
    allowed_hosts: set[str]
    server_version = "idslab-dashboard"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002  (base class name)
        logger.debug("%s %s", self.address_string(), format % args)

    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(to_jsonable(payload), allow_nan=False, default=str)  # a NaN in a file becomes null
        self._send(status, body.encode(), "application/json")

    def _error(self, status: int, message: str) -> None:
        self._json({"error": message}, status)

    def _host_ok(self) -> bool:
        return (self.headers.get("Host") or "") in self.allowed_hosts

    # ---------- GET ----------

    def do_GET(self) -> None:
        if not self._host_ok():
            return self._error(HTTPStatus.FORBIDDEN, "bad Host header")
        url = urlsplit(self.path)
        path = unquote(url.path)
        q = {k: v[-1] for k, v in parse_qs(url.query).items()}
        try:
            if path in ("/", "/index.html"):
                return self._static("index.html")
            if path.startswith("/static/"):
                return self._static(path.removeprefix("/static/"))
            if path.startswith("/files/"):
                return self._file(path.removeprefix("/files/"), download=q.get("download") == "1")
            return self._json(self._get_api(path, q))
        except PathError as e:
            return self._error(HTTPStatus.NOT_FOUND, str(e))
        except LookupError:
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        except (OSError, ValueError) as e:  # an unreadable data file, a bad query value
            return self._error(HTTPStatus.BAD_REQUEST, f"{type(e).__name__}: {e}")

    def _get_api(self, path: str, q: dict) -> Any:
        app = self.app
        if path == "/api/meta":
            return app.meta()
        if path == "/api/configs":
            return app.ws.configs()
        if path == "/api/config":
            return {"path": q.get("path"), "yaml": app.ws.config_text(q.get("path", ""))}
        if path == "/api/templates":
            return _templates()
        if path == "/api/runs":
            return app.ws.experiments()
        if path == "/api/run":
            return app.ws.experiment(q.get("path", ""))
        if path == "/api/unit":
            return app.ws.unit(q.get("path", ""))
        if path == "/api/files":
            return app.ws.listdir(q.get("path", ""))
        if path == "/api/job":
            return app.job.snapshot(int(q["tail"]) if q.get("tail", "").isdigit() else None)
        if path == "/api/catalog":
            return app.catalog()
        if path == "/api/datafiles":
            return app.ws.data_files()
        if path == "/api/dataset":
            return inspect_data(app.ws.resolve(q.get("path", ""), kind="file"))
        if path == "/api/layout":
            return app.ws.layout(q.get("path", ""))
        raise LookupError(path)

    def _static(self, name: str) -> None:
        if name not in STATIC:
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        body = (resources.files("idslab.dashboard") / "static" / name).read_bytes()
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        extra = {"Content-Security-Policy": PAGE_CSP} if name == "index.html" else None
        return self._send(HTTPStatus.OK, body, ctype, extra)

    def _file(self, rel: str, *, download: bool) -> None:
        target = self.app.ws.resolve(rel, kind="file")
        if os.path.getsize(target) > MAX_FILE:
            return self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "file too large to serve")
        ext = os.path.splitext(target)[1].lower()
        name = os.path.basename(target).replace('"', "")
        extra: dict[str, str] = {}
        if download or ext not in TEXT_TYPES | {".html"}:
            ctype = "application/octet-stream"
            extra["Content-Disposition"] = f'attachment; filename="{name}"'
        elif ext == ".html":
            ctype = "text/html; charset=utf-8"
            extra["Content-Security-Policy"] = REPORT_CSP
        else:
            ctype = "text/plain; charset=utf-8"  # never rendered as markup
            extra["Content-Security-Policy"] = "default-src 'none'"
        with open(target, "rb") as f:
            return self._send(HTTPStatus.OK, f.read(), ctype, extra)

    # ---------- POST ----------

    def do_POST(self) -> None:
        if not self._host_ok():
            return self._error(HTTPStatus.FORBIDDEN, "bad Host header")
        origin = self.headers.get("Origin")
        if origin is not None and urlsplit(origin).netloc not in self.allowed_hosts:
            return self._error(HTTPStatus.FORBIDDEN, "cross-origin request refused")
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "expected application/json")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._error(HTTPStatus.BAD_REQUEST, "bad Content-Length")
        if length > MAX_BODY:
            return self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "body too large")
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._error(HTTPStatus.BAD_REQUEST, "invalid JSON")
        if not isinstance(body, dict):
            return self._error(HTTPStatus.BAD_REQUEST, "expected a JSON object")
        app = self.app
        path = urlsplit(self.path).path
        try:
            if path == "/api/validate":
                return self._json(app.validate(str(body.get("yaml", "")), body.get("path") or None))
            if path == "/api/config/save":
                text = str(body.get("yaml", ""))
                rel = str(body.get("path", ""))
                rel = app.ws.save_config(rel, text, overwrite=bool(body.get("overwrite")))
                return self._json({"path": rel, **app.validate(text, rel)})
            if path == "/api/parse":
                try:
                    raw = parse_yaml(str(body.get("yaml", "")))
                except ConfigError as e:
                    return self._json({"ok": False, "error": str(e)})
                if not isinstance(raw, dict):
                    return self._json({"ok": False, "error": "the config must be a mapping"})
                return self._json({"ok": True, "config": raw})
            if path == "/api/dump":
                raw = body.get("config")
                if not isinstance(raw, dict):
                    return self._error(HTTPStatus.BAD_REQUEST, "config must be a JSON object")
                text = dump_yaml(raw)
                return self._json({"yaml": text, **app.validate(text, body.get("path") or None)})
            if path == "/api/layout":
                positions = body.get("positions")
                if not isinstance(positions, dict):
                    return self._error(HTTPStatus.BAD_REQUEST, "positions must be a JSON object")
                app.ws.save_layout(str(body.get("path", "")), positions)
                return self._json({"ok": True})
            if path == "/api/job":
                argv, info = build_command(app.ws, body)
                app.job.start(argv, info)
                return self._json(app.job.snapshot())
            if path == "/api/job/stop":
                app.job.stop()
                return self._json(app.job.snapshot())
        except (PathError, JobError, ConfigError, ImportError) as e:
            status = HTTPStatus.CONFLICT if "already running" in str(e) else HTTPStatus.BAD_REQUEST
            return self._error(status, str(e))
        except OSError as e:
            return self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"{type(e).__name__}: {e}")
        return self._error(HTTPStatus.NOT_FOUND, "not found")


def make_server(host: str, port: int, app: App) -> ThreadingHTTPServer:
    hosts = {"127.0.0.1", "localhost", "[::1]", host}
    allowed = {f"{h}:{port}" for h in hosts}
    handler = type("BoundHandler", (Handler,), {"app": app, "allowed_hosts": allowed})
    server = ThreadingHTTPServer((host, port), handler)
    if port == 0:  # an ephemeral port (tests): allow the one the OS picked
        real = server.server_address[1]
        handler.allowed_hosts = {f"{h}:{real}" for h in hosts}
    return server
