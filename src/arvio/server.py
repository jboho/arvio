"""The labeling page: a small HTTP server on 127.0.0.1 for one person.

It serves one HTML page and a JSON API. Item text is untrusted (transcripts,
agent output), so the page renders it as text only. Every response is built
from an allow-list of keys: the judge answer and meta stay on this side until
the session's reveal, and meta never leaves.
"""

from __future__ import annotations

import contextlib
import getpass
import hashlib
import hmac
import json
import os
import secrets
import sys
import threading
import traceback
import webbrowser
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any

from arvio import store
from arvio.report import outcome
from arvio.task import (
    BLOCK_SCHEMAS,
    LABELS_FILE,
    PACE_WINDOW,
    SECONDS,
    Card,
    Item,
    Option,
    Task,
    check_answer,
    check_task,
    short_hash,
    strict_loads,
)

MAX_BODY = 1 << 20
MAX_NOTE = 4000
TOKEN_HEADER = "X-Arvio-Token"
SERVER_LOCK = "server.lock"
FONTS = (
    "space-grotesk.woff2",
    "plex-mono-400.woff2",
    "plex-mono-500.woff2",
    "plex-mono-600.woff2",
)
LABEL_KEYS = {
    "session",
    "item_id",
    "item_hash",
    "answer",
    "followups",
    "note",
    "seconds",
}
BROKEN = "this task has errors; run `arvio check` on its folder"


class ApiError(Exception):
    def __init__(self, status: int, message: str, problems: list[str] | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.problems = problems or []


@dataclass
class Session:
    id: str
    task_id: str
    version: str
    item_ids: list[str]


def task_id(folder: Path) -> str:
    return short_hash(str(folder))


def order_key(version: str, item_id: str) -> str:
    """Items come in hash order: never the file's or the judge's order."""
    return hashlib.sha256(f"{version}:{item_id}".encode()).hexdigest()


def labeler_name() -> str:
    try:
        return os.environ.get("ARVIO_LABELER") or getpass.getuser()
    except (OSError, KeyError, ImportError):
        # getpass raises KeyError before Python 3.13 when the uid has no
        # passwd entry (some containers), and ImportError without pwd.
        return ""


def _option(o: Option) -> dict:
    return {
        "key": o.key,
        "hotkey": o.hotkey,
        "label": o.label,
        "meaning": o.meaning,
        "example": o.example,
    }


def card_view(card: Card) -> dict:
    """What the page may know about a card. The agreement map waits for Finish."""
    return {
        "title": card.title,
        "purpose": card.purpose,
        "question": card.question,
        "rules": list(card.rules),
        "session_size": card.session_size,
        "version": card.version,
        "has_judge": card.agreement is not None,
        "choices": [_option(c) for c in card.choices],
        "followups": [
            {
                "key": f.key,
                "when": list(f.when),
                "kind": f.kind,
                "prompt": f.prompt,
                "required": f.required,
                "options": [_option(o) for o in f.options],
                "fields": [
                    {"key": x.key, "label": x.label, "multiline": x.multiline}
                    for x in f.fields
                ],
            }
            for f in card.followups
        ],
    }


def item_view(item: Item) -> dict:
    """What the page may know about an item before the reveal: never judge or meta."""
    return {
        "id": item.id,
        "hash": item.hash,
        "display": [
            {k: v for k, v in block.items() if k in BLOCK_SCHEMAS[block["kind"]]}
            for block in item.display
        ],
        "prefill": dict(item.prefill),
    }


class App:
    """State shared by all requests.

    Every API call runs under one lock, so the label log has one writer at a
    time. The server still answers on threads: a browser's idle preconnect
    would stall a single-threaded server until it timed out.
    """

    def __init__(self, labeler: str | None = None):
        self.token = secrets.token_urlsafe(32)
        self.labeler = labeler_name() if labeler is None else labeler
        self.port = 0
        self.lock = threading.Lock()
        self.folders: dict[str, Path] = {}
        self.sessions: dict[str, Session] = {}
        self._held: set[Path] = set()
        self._stack = contextlib.ExitStack()
        page = resources.files("arvio") / "static" / "index.html"
        self._page = page.read_text(encoding="utf-8")

    def open(self) -> None:
        """Take the server lock and lock every registered folder.

        Raises StoreError if another arvio is running on this inbox.
        """
        root = store.data_dir()
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            self._stack.enter_context(store.locked(root / SERVER_LOCK, wait=False))
        except store.StoreError:
            self._stack.close()
            raise store.StoreError(
                "another arvio is already running for this inbox; use that page, "
                "or stop it with Ctrl-C in its terminal"
            ) from None
        self.refresh()

    def close(self) -> None:
        self._stack.close()
        self._held.clear()

    def refresh(self) -> list[dict] | None:
        """Re-read the registry; lock folders added since the last read."""
        entries = store.read_registry()
        for entry in entries or []:
            folder = Path(entry["dir"])
            self.folders[task_id(folder)] = folder
            self._lock(folder)
        return entries

    def _lock(self, folder: Path) -> bool:
        if folder in self._held:
            return True
        if not folder.is_dir():
            return False
        try:
            self._stack.enter_context(store.locked(folder, wait=False))
        except store.StoreError:
            return False
        self._held.add(folder)
        return True

    # Pages and API.

    def page(self, nonce: str) -> bytes:
        return self._page.replace("{{nonce}}", nonce).encode()

    def inbox(self) -> dict:
        entries = self.refresh()
        if entries is None:
            raise ApiError(500, "the task registry can't be read; run `arvio status`")
        tasks = []
        for entry in entries:
            folder = Path(entry["dir"])
            view = {"id": task_id(folder), "dir": str(folder), "name": folder.name}
            if not folder.is_dir():
                view["problem"] = "the folder is gone; run `arvio remove` on it"
            elif not self._lock(folder):
                view["problem"] = "open in another arvio"
            else:
                task, _ = check_task(folder)
                if task is None:
                    view["problem"] = BROKEN
                else:
                    view.update(task.summary())
            tasks.append(view)
        return {"tasks": tasks}

    def _task(self, tid: str) -> Task:
        folder = self.folders.get(tid)
        if folder is None:
            raise ApiError(404, "no such task")
        if not self._lock(folder):
            raise ApiError(409, "this task is open in another arvio")
        task, _ = check_task(folder)
        if task is None:
            raise ApiError(409, BROKEN)
        return task

    def _session(self, tid: str, sid: Any) -> Session:
        s = self.sessions.get(sid) if isinstance(sid, str) else None
        if s is None or s.task_id != tid:
            raise ApiError(400, "unknown session; go back to the inbox and start again")
        return s

    def task(self, tid: str) -> dict:
        task = self._task(tid)
        return {"id": tid, "card": card_view(task.card), "summary": task.summary()}

    def start(self, tid: str, body: dict) -> dict:
        _no_unknown_keys(body, set())
        task = self._task(tid)
        waiting = sorted(
            task.waiting(), key=lambda i: order_key(task.card.version, i.id)
        )
        chosen = waiting[: task.card.session_size]
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        sid = f"s-{stamp}-{secrets.token_hex(3)}"
        self.sessions[sid] = Session(
            sid, tid, task.card.version, [i.id for i in chosen]
        )
        return {
            "session": sid,
            "card": card_view(task.card),
            "items": [item_view(i) for i in chosen],
            "waiting": len(waiting),
            "seconds_per_item": task.card.seconds_per_item,
            "recent_seconds": task.labels.seconds[-PACE_WINDOW:],
        }

    def label(self, tid: str, body: dict) -> dict:
        _no_unknown_keys(body, LABEL_KEYS)
        session = self._session(tid, body.get("session"))
        item_id = body.get("item_id")
        if item_id not in session.item_ids:
            raise ApiError(400, "that item is not in this session")
        answer = body.get("answer")
        followups = body.get("followups", {})
        note = body.get("note", "")
        seconds = body.get("seconds")
        if not isinstance(answer, str):
            raise ApiError(400, "answer: must be a choice key")
        if not isinstance(body.get("item_hash"), str):
            raise ApiError(400, "item_hash: send the hash of the item shown")
        if not isinstance(note, str) or len(note) > MAX_NOTE:
            raise ApiError(400, f"note: must be text of at most {MAX_NOTE} characters")
        what, ok = SECONDS
        if not ok(seconds):
            raise ApiError(400, f"seconds: must be {what}")
        task = self._task(tid)
        if task.card.version != session.version:
            raise ApiError(409, "the task card changed; go back to the inbox")
        item = task.item(item_id)
        if item is None or item.hash != body.get("item_hash"):
            raise ApiError(409, "this item changed on disk; go back to the inbox")
        if item_id in task.labels.revealed:
            raise ApiError(
                409, "the judge's answer for this item was shown; it's locked"
            )
        errs = check_answer(task.card, answer, followups, item.prefill)
        if errs:
            raise ApiError(400, "the answer doesn't fit the card", errs)
        store.append_jsonl(
            task.dir / LABELS_FILE,
            {
                "kind": "label",
                "item_id": item_id,
                "item_hash": item.hash,
                "task_version": task.card.version,
                "answer": answer,
                "followups": followups,
                "note": note.strip(),
                "seconds": round(float(seconds), 1),
                "labeled_at": store.now(),
                "labeler": self.labeler,
                "session": session.id,
            },
        )
        return {"ok": True}

    def finish(self, tid: str, body: dict) -> dict:
        """Lock every labeled, unrevealed item, then send the judge's answers.

        Labels left by a closed tab are revealed here too, so none are stranded.
        """
        _no_unknown_keys(body, {"session"})
        session = self._session(tid, body.get("session"))
        task = self._task(tid)
        if task.card.version != session.version:
            raise ApiError(409, "the task card changed; go back to the inbox")
        rank = {item_id: n for n, item_id in enumerate(session.item_ids)}
        ids = sorted(
            (i for i in task.labels.current if i not in task.labels.revealed),
            key=lambda i: (rank.get(i, len(rank)), i),
        )
        if ids:
            store.append_jsonl(
                task.dir / LABELS_FILE,
                {
                    "kind": "reveal",
                    "item_ids": ids,
                    "task_version": task.card.version,
                    "at": store.now(),
                    "session": session.id,
                },
            )
        card = task.card
        results = []
        for item_id in ids:
            item = task.item(item_id)
            label = task.labels.current[item_id]
            results.append(
                {
                    **item_view(item),
                    "answer": label["answer"],
                    "followups": label.get("followups", {}),
                    "note": label.get("note", ""),
                    "judge": item.judge,
                    "agrees": outcome(card, label["answer"], item.judge),
                }
            )
        compared = [r for r in results if r["agrees"] is not None]
        return {
            "has_judge": card.agreement is not None,
            "judge_field": card.agreement.judge_field if card.agreement else None,
            "compare": [
                {"followup": f, "judge_field": j}
                for f, j in (card.agreement.compare if card.agreement else ())
            ],
            "judge_map": (
                {k: list(v) for k, v in card.agreement.map.items()}
                if card.agreement
                else {}
            ),
            "revealed": len(results),
            "compared": len(compared),
            "agreed": sum(1 for r in compared if r["agrees"]),
            "items": results,
        }


def _no_unknown_keys(body: dict, allowed: set[str]) -> None:
    unknown = sorted(body.keys() - allowed)
    if unknown:
        raise ApiError(400, f"unknown keys: {', '.join(unknown)}")


class Handler(BaseHTTPRequestHandler):
    server_version = "arvio"
    sys_version = ""

    @property
    def app(self) -> App:
        return self.server.app

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        self._serve("GET")

    def do_POST(self) -> None:
        self._serve("POST")

    def _send(
        self, status: int, body: bytes, content_type: str, extra: dict | None = None
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data: dict) -> None:
        body = json.dumps(data, ensure_ascii=False).encode()
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, status: int, message: str, problems: list[str] | None = None):
        self._json(status, {"error": message, "problems": problems or []})

    def _origin_ok(self) -> bool:
        """Host must name this server; an Origin, when sent, must too."""
        port = self.app.port
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host") not in hosts:
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin in {f"http://{h}" for h in hosts}

    def _body(self) -> dict:
        try:
            size = int(self.headers.get("Content-Length", ""))
        except ValueError:
            raise ApiError(411, "send a Content-Length") from None
        if size < 0 or size > MAX_BODY:
            raise ApiError(413, "request too large")
        if size == 0:
            return {}
        try:
            data = strict_loads(self.rfile.read(size).decode("utf-8"))
        except (ValueError, RecursionError):
            raise ApiError(400, "the body is not valid JSON") from None
        if not isinstance(data, dict):
            raise ApiError(400, "the body must be a JSON object")
        return data

    def _serve(self, method: str) -> None:
        if not self._origin_ok():
            self._error(403, "wrong host")
            return
        path = self.path.split("?", 1)[0]
        if method == "GET" and path == "/":
            nonce = secrets.token_urlsafe(16)
            csp = (
                f"default-src 'none'; script-src 'nonce-{nonce}'; "
                f"style-src 'nonce-{nonce}'; font-src 'self'; connect-src 'self'; "
                "img-src data:; base-uri 'none'; form-action 'none'; "
                "frame-ancestors 'none'"
            )
            self._send(
                200,
                self.app.page(nonce),
                "text/html; charset=utf-8",
                {"Content-Security-Policy": csp},
            )
            return
        if method == "GET" and path.startswith("/fonts/"):
            name = path.removeprefix("/fonts/")
            if name not in FONTS:
                self._error(404, "not found")
                return
            font = resources.files("arvio") / "static" / "fonts" / name
            self._send(200, font.read_bytes(), "font/woff2")
            return
        if not path.startswith("/api/"):
            self._error(404, "not found")
            return
        given = self.headers.get(TOKEN_HEADER, "")
        if not hmac.compare_digest(given.encode(), self.app.token.encode()):
            self._error(403, "wrong or missing key; open the link arvio printed")
            return
        try:
            body = self._body() if method == "POST" else {}
            with self.app.lock:
                data = self._route(method, path.removeprefix("/api").split("/"), body)
        except ApiError as e:
            self._error(e.status, e.message, e.problems)
            return
        except Exception as e:
            # A failed disk write (full, read-only, EIO) must still answer, or
            # the page reports that arvio stopped. Only an OSError's text goes
            # back: other messages could quote item data, the judge's included.
            traceback.print_exc(file=sys.stderr)
            why = str(e) if isinstance(e, OSError) else type(e).__name__
            self._error(500, f"arvio could not do that ({why}); see its terminal")
            return
        self._json(200, data)

    def _route(self, method: str, parts: list[str], body: dict) -> dict:
        app = self.app
        match method, parts:
            case "GET", ["", "inbox"]:
                return app.inbox()
            case "GET", ["", "tasks", tid]:
                return app.task(tid)
            case "POST", ["", "tasks", tid, "sessions"]:
                return app.start(tid, body)
            case "POST", ["", "tasks", tid, "labels"]:
                return app.label(tid, body)
            case "POST", ["", "tasks", tid, "finish"]:
                return app.finish(tid, body)
        raise ApiError(404, "not found")


def make_server(app: App) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.daemon_threads = True
    httpd.app = app
    app.port = httpd.server_address[1]
    return httpd


def run(open_browser: bool = True) -> int:
    """Serve the inbox until Ctrl-C."""
    app = App()
    try:
        app.open()
    except store.StoreError as e:
        print(f"arvio: {e}", file=sys.stderr)
        return 1
    httpd = make_server(app)
    url = f"http://127.0.0.1:{app.port}/#t={app.token}"
    print(f"arvio is running at {url}\nPress Ctrl-C to stop.", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
        app.close()
    return 0
