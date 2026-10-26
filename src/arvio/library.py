"""arvio's own copy of each task: import a project's folder, archive a finished one."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path

from arvio.store import atomic_write, data_dir, locked, unregister
from arvio.task import CARD_FILE, ITEMS_FILE, LABELS_FILE, Problem, Task, check_task

NAME_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")


class LibraryError(Exception):
    """A task copy arvio refuses to make or move, with the reason."""

    def __init__(self, message: str, problems: list[Problem] | None = None):
        super().__init__(message)
        self.problems = problems or []


def tasks_root() -> Path:
    return data_dir() / "tasks"


def archive_root() -> Path:
    return data_dir() / "archive"


def _name(name: str) -> str:
    if not NAME_RE.fullmatch(name):
        raise LibraryError(
            f"{name!r} is not a usable task name: use 1-64 of a-z 0-9 . _ -, "
            "starting with a letter or digit (pass --name to choose one)"
        )
    return name


def import_task(source: Path, name: str | None = None) -> tuple[Task, bool]:
    """Copy the card and items of a project's task folder into arvio's library.

    Returns the library copy and whether it is new. When the copy exists, only
    the card and items are replaced; its labels stay, and the import is refused
    if a labeled item would disappear (the usual task check says so).
    """
    source = Path(source).expanduser().resolve()
    task, problems = check_task(source)
    if task is None:
        raise LibraryError("not imported: fix the errors above first", problems)
    name = _name(name if name is not None else source.name)
    root, dest = tasks_root(), tasks_root() / name
    if (archive_root() / name).exists():
        raise LibraryError(f"{name!r} is already in the archive; choose another --name")
    if source == dest.resolve():
        raise LibraryError("that folder is already arvio's copy; use arvio add")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=root, prefix=".import-"))
    try:
        for file in (CARD_FILE, ITEMS_FILE):
            shutil.copyfile(source / file, staging / file)
        existed = dest.is_dir()
        if existed and (dest / LABELS_FILE).exists():
            shutil.copyfile(dest / LABELS_FILE, staging / LABELS_FILE)
        merged, problems = check_task(staging)
        if merged is None:
            raise LibraryError(
                "not imported: the labels already here would break", problems
            )
        if not existed:
            staging.chmod(0o755)
            os.rename(staging, dest)
        else:
            with locked(dest, wait=False):
                for file in (CARD_FILE, ITEMS_FILE):
                    atomic_write(
                        dest / file, (staging / file).read_text(encoding="utf-8")
                    )
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    task, problems = check_task(dest)
    if task is None:
        raise LibraryError("the copy in the library does not check out", problems)
    return task, not existed


def archive_task(name: str) -> Path:
    """Take a library task off the inbox and move it, labels and reports included."""
    name = _name(name)
    source = (tasks_root() / name).resolve()
    if not source.is_dir():
        raise LibraryError(f"no task named {name!r} in {tasks_root()}")
    target = archive_root() / name
    if target.exists():
        raise LibraryError(f"{target} already exists; nothing moved")
    archive_root().mkdir(mode=0o700, parents=True, exist_ok=True)
    with locked(source, wait=False):
        unregister(source)
        os.rename(source, target)
    return target
