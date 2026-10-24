# SPDX-License-Identifier: GPL-3.0-or-later
"""Stdlib HTTP server for the Melton transcode GUI."""

from __future__ import annotations

import json
import shlex
import subprocess
import threading
import uuid
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ready2rip.transcode.command import (
    OptionError,
    build_argv,
    catalog,
    options_from_dict,
)
from ready2rip.transcode.deps import dependencies_as_dicts, dependencies_ok_for
from ready2rip.transcode.probe import probe_media

STATIC_DIR = Path(__file__).resolve().parent / "static"

_jobs_lock = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {}


class TranscodeHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        # Quieter than default; still useful for debug
        print(f"[transcode-ui] {self.address_string()} {fmt % args}")

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/deps":
            self._json(dependencies_as_dicts())
            return
        if parsed.path == "/api/catalog":
            self._json(catalog())
            return
        if parsed.path.startswith("/api/jobs/"):
            job_id = parsed.path.rsplit("/", 1)[-1]
            with _jobs_lock:
                job = _jobs.get(job_id)
            if not job:
                self._json({"error": "job not found"}, HTTPStatus.NOT_FOUND)
                return
            self._json(job)
            return
        if parsed.path in ("/", "/index.html"):
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._json({"error": "invalid JSON"}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/probe":
            path = (data.get("path") or "").strip()
            if not path:
                self._json({"error": "path required"}, HTTPStatus.BAD_REQUEST)
                return
            try:
                self._json(probe_media(path))
            except Exception as exc:  # noqa: BLE001 — surface to UI
                self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/preview":
            try:
                opts = options_from_dict(data)
                argv, cwd = build_argv(opts)
                self._json(
                    {
                        "argv": argv,
                        "command": " ".join(shlex.quote(a) for a in argv),
                        "cwd": str(cwd) if cwd else None,
                    }
                )
            except OptionError as exc:
                self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/run":
            try:
                opts = options_from_dict(data)
                ok, _ = dependencies_ok_for(opts.tool)
                if not ok:
                    self._json(
                        {"error": "missing required dependencies; see /api/deps"},
                        HTTPStatus.BAD_REQUEST,
                    )
                    return
                argv, cwd = build_argv(opts)
            except OptionError as exc:
                self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return

            job_id = uuid.uuid4().hex[:12]
            job = {
                "id": job_id,
                "status": "running",
                "argv": argv,
                "command": " ".join(shlex.quote(a) for a in argv),
                "cwd": str(cwd) if cwd else None,
                "returncode": None,
                "stdout": "",
                "stderr": "",
            }
            with _jobs_lock:
                _jobs[job_id] = job
            threading.Thread(
                target=_run_job,
                args=(job_id, argv, cwd),
                daemon=True,
            ).start()
            self._json({"id": job_id, **{k: job[k] for k in ("status", "command", "cwd")}})
            return

        self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def _json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def _run_job(job_id: str, argv: list[str], cwd: Path | None) -> None:
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            check=False,
        )
        with _jobs_lock:
            job = _jobs[job_id]
            job["status"] = "ok" if proc.returncode == 0 else "failed"
            job["returncode"] = proc.returncode
            job["stdout"] = proc.stdout or ""
            job["stderr"] = proc.stderr or ""
    except Exception as exc:  # noqa: BLE001
        with _jobs_lock:
            job = _jobs[job_id]
            job["status"] = "failed"
            job["returncode"] = -1
            job["stderr"] = str(exc)


def serve(host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    if not STATIC_DIR.is_dir():
        raise RuntimeError(f"static UI missing: {STATIC_DIR}")
    httpd = ThreadingHTTPServer((host, port), TranscodeHandler)
    return httpd
