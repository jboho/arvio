"""File writes that survive a crash, and the registry of tasks in the inbox."""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import tempfile
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

REGISTRY_FILE = "tasks.json"
REGISTRY_FORMAT = 1


class StoreError(Exception):
    """A shared file arvio refuses to write, with the reason."""


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def data_dir() -> Path:
    return Path(os.environ.get("ARVIO_DATA_DIR") or Path.home() / ".arvio").expanduser()


def atomic_write(path: Path, text: str, mode: int = 0o644) -> None:
    """Write to a temp file in the same folder, sync it, then rename it into place."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            _sync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise
    _fsync_dir(path.parent)


def append_jsonl(path: Path, row: dict) -> None:
    """Append one JSON line and sync it to disk before returning.

    Callers hold the file's lock. A last line without its newline is left by
    a write that was cut short. If it parses, it only lacks the newline. If
    not, it moves to PATH.torn, so the log stays readable and the text is kept.
    """
    data = (json.dumps(row, ensure_ascii=False) + "\n").encode()
    created = not path.exists()
    fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        size = os.fstat(fd).st_size
        if size and os.pread(fd, 1, size - 1) != b"\n":
            text = os.pread(fd, size, 0)
            start = text.rfind(b"\n") + 1
            tail = text[start:]
            if not tail.strip() or _parses(tail):
                data = b"\n" + data
            else:
                _append_bytes(path.with_name(path.name + ".torn"), tail + b"\n")
                os.ftruncate(fd, start)
        _write_all(fd, data)
        _sync(fd)
    finally:
        os.close(fd)
    if created:
        _fsync_dir(path.parent)


def _parses(raw: bytes) -> bool:
    try:
        json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError):
        return False
    return True


def _append_bytes(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        _write_all(fd, data)
        _sync(fd)
    finally:
        os.close(fd)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


def _sync(fd: int) -> None:
    """Flush a file to the drive. On macOS, fsync stops at the drive's own
    cache, which a power cut loses; F_FULLFSYNC flushes that too."""
    if hasattr(fcntl, "F_FULLFSYNC"):
        try:
            fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
            return
        except OSError:
            pass
    os.fsync(fd)


def _fsync_dir(folder: Path) -> None:
    fd = os.open(folder, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextlib.contextmanager
def locked(path: Path, wait: bool = True) -> Iterator[None]:
    """Hold an exclusive flock on path (a file or a folder) for the block.

    With wait=False, raise StoreError at once if another process holds it.
    """
    fd = os.open(path, os.O_RDONLY if path.is_dir() else os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError:
            raise StoreError(f"{path} is in use by another arvio") from None
        yield
    finally:
        os.close(fd)


def _stat(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    return st.st_mtime_ns, st.st_size


def read_registry() -> list[dict] | None:
    """Registered tasks; [] when there is no registry yet, None if it is unreadable."""
    file = data_dir() / REGISTRY_FILE
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, UnicodeDecodeError, ValueError, RecursionError):
        return None
    if not isinstance(data, dict) or data.get("format") != REGISTRY_FORMAT:
        return None
    tasks = data.get("tasks")
    if not isinstance(tasks, list) or not all(
        isinstance(t, dict) and isinstance(t.get("dir"), str) for t in tasks
    ):
        return None
    return tasks


def update_registry(change: Callable[[list[dict]], list[dict]]) -> list[dict]:
    """Apply change to the registry under a lock, then write it atomically.

    Refuses if the registry is unreadable, or if it changed on disk (a hand
    edit) between the read and the write.
    """
    root = data_dir()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    file = root / REGISTRY_FILE
    with locked(root / ".lock"):
        before = _stat(file)
        tasks = read_registry()
        if tasks is None:
            raise StoreError(f"{file} is not a registry arvio can read; fix or move it")
        updated = change(list(tasks))
        if _stat(file) != before:
            raise StoreError(f"{file} changed while arvio was updating it; try again")
        payload = {"format": REGISTRY_FORMAT, "tasks": updated}
        atomic_write(file, json.dumps(payload, indent=2) + "\n", mode=0o600)
    return updated


def register(folder: Path) -> bool:
    """Add a task folder to the inbox. False if it was already there."""
    key = str(folder)
    added = False

    def change(tasks: list[dict]) -> list[dict]:
        nonlocal added
        if any(t["dir"] == key for t in tasks):
            return tasks
        added = True
        return [*tasks, {"dir": key, "added_at": now()}]

    update_registry(change)
    return added


def unregister(folder: Path) -> bool:
    """Take a task folder off the inbox. False if it wasn't there."""
    key = str(folder)
    removed = False

    def change(tasks: list[dict]) -> list[dict]:
        nonlocal removed
        kept = [t for t in tasks if t["dir"] != key]
        removed = len(kept) != len(tasks)
        return kept

    update_registry(change)
    return removed
