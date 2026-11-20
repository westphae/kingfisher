"""Local web UI to browse kingfisher flight DBs and plot time series.

    uv run --project analysis python scripts/analyze_flights.py serve
"""

from __future__ import annotations

import json
import mimetypes
import re
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from analysis.browse import list_sessions, resolve_db, session_detail
from analysis.clockalign import load_clock
from analysis.db import connect_ro
from analysis.engine.limits import load_limits
from analysis.engine.run import review_all, review_db
from analysis.engine.summary import summarize
from analysis.health import analyze_health
from analysis.jpi.duplicates import flag_sessions
from analysis.series import DEFAULT_POINTS, query_series

STATIC_DIR = Path(__file__).resolve().parent / "static"


def _json(obj: Any) -> bytes:
    return json.dumps(obj, allow_nan=False).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    flights_dir: Path
    catalog_path: Path
    engine_cache: Path | None = None
    server_version = "kingfisher-analysis/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} {fmt % args}")

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, code: int, obj: Any) -> None:
        self._send(code, _json(obj), "application/json; charset=utf-8")

    def _err(self, code: int, msg: str) -> None:
        self._send_json(code, {"error": msg})

    def do_GET(self) -> None:  # noqa: N802
        try:
            self._dispatch()
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._err(500, str(e))

    def _dispatch(self) -> None:
        u = urlparse(self.path)
        path = unquote(u.path)
        q = {k: v[-1] if v else "" for k, v in parse_qs(u.query).items()}

        if path in ("/", "/index.html"):
            self._static("index.html")
            return
        if path.startswith("/static/"):
            self._static(path[len("/static/") :])
            return
        if path == "/api/sessions":
            self._send_json(
                200,
                {"sessions": list_sessions(self.flights_dir, self.catalog_path)},
            )
            return

        m = re.fullmatch(r"/api/sessions/([^/]+)", path)
        if m:
            self._detail(m.group(1))
            return
        m = re.fullmatch(r"/api/sessions/([^/]+)/health", path)
        if m:
            self._health(m.group(1))
            return
        m = re.fullmatch(r"/api/sessions/([^/]+)/series", path)
        if m:
            self._series(m.group(1), q)
            return
        m = re.fullmatch(r"/api/sessions/([^/]+)/engine", path)
        if m:
            self._engine(m.group(1))
            return
        if path == "/api/engine/summary":
            self._engine_summary()
            return
        self._err(404, "not found")

    def _static(self, rel: str) -> None:
        rel = rel.lstrip("/")
        if not rel or ".." in Path(rel).parts:
            self._err(404, "not found")
            return
        path = (STATIC_DIR / rel).resolve()
        try:
            path.relative_to(STATIC_DIR.resolve())
        except ValueError:
            self._err(404, "not found")
            return
        if not path.is_file():
            self._err(404, "not found")
            return
        data = path.read_bytes()
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix == ".js":
            ctype = "text/javascript; charset=utf-8"
        elif path.suffix == ".css":
            ctype = "text/css; charset=utf-8"
        elif path.suffix == ".html":
            ctype = "text/html; charset=utf-8"
        self._send(200, data, ctype)

    def _db(self, name: str) -> Path:
        return resolve_db(self.flights_dir, name)

    def _detail(self, name: str) -> None:
        try:
            path = self._db(name)
        except FileNotFoundError:
            self._err(404, "no such session")
            return
        except ValueError as e:
            self._err(400, str(e))
            return
        self._send_json(200, session_detail(path))

    def _health(self, name: str) -> None:
        try:
            path = self._db(name)
        except FileNotFoundError:
            self._err(404, "no such session")
            return
        except ValueError as e:
            self._err(400, str(e))
            return
        self._send_json(200, analyze_health(path).to_json())

    def _series(self, name: str, q: dict[str, str]) -> None:
        try:
            path = self._db(name)
        except FileNotFoundError:
            self._err(404, "no such session")
            return
        except ValueError as e:
            self._err(400, str(e))
            return
        table = q.get("table", "")
        column = q.get("col") or q.get("column") or ""
        if not table or not column:
            self._err(400, "table and col required")
            return
        try:
            t0 = int(q["t0"])
            t1 = int(q["t1"])
        except (KeyError, ValueError):
            self._err(400, "t0 and t1 (ns) required")
            return
        n = int(q.get("n") or DEFAULT_POINTS)
        align = q.get("align", "1") not in ("0", "false", "no")
        conn = connect_ro(path)
        try:
            payload = query_series(
                conn, table, column, t0_ns=t0, t1_ns=t1, n=n, align=align
            )
        except ValueError as e:
            self._err(400, str(e))
            return
        finally:
            conn.close()
        payload["t"] = [None if v is None else v / 1e9 for v in payload.pop("ts_ns")]
        payload["y"] = payload.pop("values")
        self._send_json(200, payload)

    def _engine(self, name: str) -> None:
        try:
            path = self._db(name)
        except FileNotFoundError:
            self._err(404, "no such session")
            return
        except ValueError as e:
            self._err(400, str(e))
            return
        review = review_db(path, load_limits(None), self.engine_cache)
        self._send_json(200, _align_review(path, review))

    def _engine_summary(self) -> None:
        limits = load_limits(None)
        reviews, stats = review_all(self.flights_dir, limits, cache_dir=self.engine_cache)
        if stats.get("n_reviewed") == 0 and self.engine_cache is not None:
            prev = self.engine_cache / "summary.json"
            try:
                flags = json.loads(prev.read_text(encoding="utf-8")).get("flags") or []
            except (OSError, json.JSONDecodeError):
                flags = flag_sessions(self.flights_dir) if self.flights_dir.is_dir() else []
        else:
            flags = flag_sessions(self.flights_dir) if self.flights_dir.is_dir() else []
        self._send_json(200, summarize(reviews, flags))


def _align_review(path: Path, review: dict) -> dict:
    """Shift event and phase times onto the same clock the chart uses.

    The cache keeps raw ``ts_ns``. This copy is what the browser zooms to.
    """
    out = json.loads(json.dumps(review))
    conn = connect_ro(path)
    try:
        model = load_clock(conn)
    finally:
        conn.close()
    if not model.needs_align:
        return out

    def shift(ns):
        if ns is None:
            return None
        return int(model.align_ts_ns(int(ns)))

    for phase in out.get("phases") or []:
        phase["t0_ns"] = shift(phase.get("t0_ns"))
        phase["t1_ns"] = shift(phase.get("t1_ns"))
    for event in out.get("events") or []:
        raw = event.get("t_ns")
        aligned = shift(raw)
        if raw is not None and aligned is not None:
            delta_s = (aligned - int(raw)) / 1e9
            if event.get("t_unix") is not None:
                event["t_unix"] = float(event["t_unix"]) + delta_s
        event["t_ns"] = aligned
        event["t0_ns"] = shift(event.get("t0_ns"))
        event["t1_ns"] = shift(event.get("t1_ns"))
    return out


def serve(
    *,
    flights_dir: Path,
    catalog_path: Path,
    host: str = "0.0.0.0",
    port: int = 8765,
) -> None:
    Handler.flights_dir = flights_dir
    Handler.catalog_path = catalog_path
    Handler.engine_cache = catalog_path.parent / "engine"
    ThreadingHTTPServer.allow_reuse_address = True
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"Flight analysis UI → http://{host}:{port}")
    print(f"  flights: {flights_dir}")
    print(f"  catalog: {catalog_path}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
        httpd.server_close()
