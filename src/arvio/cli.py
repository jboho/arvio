"""The arvio command."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from importlib import resources
from pathlib import Path

from arvio import __version__, library, report, server
from arvio.store import (
    StoreError,
    atomic_write,
    read_registry,
    register,
    set_archived,
    unregister,
)
from arvio.task import Problem, check_task

GUIDE_URL = "https://github.com/jboho/arvio/blob/main/docs/add-a-task.md"


def _print_guide(file=None) -> None:
    print(f"guide: {GUIDE_URL}", file=file or sys.stdout)


def _print_problems(problems: list[Problem]) -> None:
    for p in problems:
        print(p, file=sys.stderr)


def cmd_check(args: argparse.Namespace) -> int:
    task, problems = check_task(args.dir)
    _print_problems(problems)
    if task is None:
        errors = sum(1 for p in problems if not p.warning)
        print(
            f"{errors} error{'s' if errors != 1 else ''}; fix them and run check again"
        )
        _print_guide()
        return 1
    s = task.summary()
    print(
        f"ok: {s['items']} items, {s['labeled']} labeled, "
        f"card version {task.card.version}"
    )
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    if args.name and not args.import_:
        print("arvio: --name needs --import", file=sys.stderr)
        return 1
    if args.import_:
        return _import_and_add(args)
    task, problems = check_task(args.dir)
    _print_problems(problems)
    if task is None:
        print("not added: fix the errors above first")
        _print_guide()
        return 1
    try:
        added = register(task.dir)
    except (StoreError, OSError) as e:
        print(f"arvio: {e}", file=sys.stderr)
        return 1
    state = "added" if added else "already in your inbox"
    print(f"{state}: {task.card.title} ({len(task.waiting())} waiting)")
    return 0


def _import_and_add(args: argparse.Namespace) -> int:
    try:
        task, created = library.import_task(args.dir, args.name)
        register(task.dir)
    except library.LibraryError as e:
        _print_problems(e.problems)
        print(f"arvio: {e}", file=sys.stderr)
        _print_guide(sys.stderr)
        return 1
    except (StoreError, OSError) as e:
        print(f"arvio: {e}", file=sys.stderr)
        return 1
    state = "imported" if created else "updated"
    print(f"{state}: {task.card.title} ({len(task.waiting())} waiting)\n  {task.dir}")
    return 0


def _move(args: argparse.Namespace, archived: bool) -> int:
    try:
        folder = library.resolve_registered(args.target)
        reason = library.unfinished_reason(folder) if archived else None
        if reason:
            raise library.LibraryError(f"not archived: {reason}")
        changed = set_archived(folder, archived)
    except (library.LibraryError, StoreError, OSError) as e:
        print(f"arvio: {e}", file=sys.stderr)
        return 1
    where = "archived" if archived else "active"
    print(f"{'moved to' if changed else 'already'} {where}: {folder}")
    return 0


def cmd_archive(args: argparse.Namespace) -> int:
    return _move(args, True)


def cmd_unarchive(args: argparse.Namespace) -> int:
    return _move(args, False)


def cmd_remove(args: argparse.Namespace) -> int:
    folder = Path(args.dir).expanduser().resolve()
    try:
        removed = unregister(folder)
    except (StoreError, OSError) as e:
        print(f"arvio: {e}", file=sys.stderr)
        return 1
    print(f"removed: {folder}" if removed else f"not in your inbox: {folder}")
    return 0 if removed else 1


def cmd_status(args: argparse.Namespace) -> int:
    tasks = read_registry()
    if tasks is None:
        print("arvio: the task registry can't be read; fix or move it", file=sys.stderr)
        return 1
    if not tasks:
        print("No tasks yet. Add one with: arvio add DIR")
        _print_guide()
        return 0
    shown = [t for t in tasks if t.get("archived", False) == args.archived]
    hidden = len(tasks) - len(shown)
    if not shown:
        print("No archived tasks." if args.archived else "No active tasks.")
    broken = 0
    for entry in shown:
        task, problems = check_task(Path(entry["dir"]))
        if task is None:
            broken += 1
            first = next((p for p in problems if not p.warning), None)
            print(f"{entry['dir']}\n  can't open: {first.message if first else '?'}")
            print(f"  see: arvio check {entry['dir']}\n")
            continue
        s = task.summary()
        line = f"  waiting {s['waiting']}, labeled {s['labeled']}"
        if s["labeled"] and s["has_judge"]:
            line += f" (judge shown for {s['revealed']})"
        elif s["labeled"]:
            line += f" ({s['revealed']} locked)"
        if s["older"]:
            line += f", {s['older']} labeled under an older card"
        if s["last_at"]:
            line += f", last label {s['last_at'][:10]}"
        print(f"{s['title']}\n  {task.dir}\n{line}")
        if s["next_n"]:
            print(f"  next session: {s['next_n']} items, about {s['next_minutes']} min")
        print()
    if hidden and args.archived:
        print(f"{hidden} active; list them with: arvio status")
    elif hidden:
        print(f"{hidden} archived; list them with: arvio status --archived")
    return 1 if broken else 0


def cmd_report(args: argparse.Namespace) -> int:
    task, problems = check_task(args.dir)
    _print_problems(problems)
    if task is None:
        print("no report: fix the errors above first", file=sys.stderr)
        return 1
    try:
        data = report.write(task, args.by)
    except OSError as e:
        print(f"arvio: can't write the report: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print(report.markdown(task, data), end="")
        print(f"\nwrote {task.dir / 'report.md'} and report.json")
    gate = data["gate"]
    return 2 if gate and not gate["passed"] else 0


def cmd_labels(args: argparse.Namespace) -> int:
    task, problems = check_task(args.dir)
    _print_problems(problems)
    if task is None:
        print("no labels: fix the errors above first", file=sys.stderr)
        return 1
    for item in task.items:
        row = task.labels.current.get(item.id)
        if row is None:
            continue
        out = {k: v for k, v in row.items() if k != "kind"}
        out["locked"] = item.id in task.labels.revealed
        print(json.dumps(out, ensure_ascii=False))
    return 0


# PAGE -> (file in src/arvio/man, the man section folder it installs into)
MAN_PAGES = {
    "arvio": ("arvio.1", "man1"),
    "task": ("arvio-task.5", "man5"),
}


def _man_source(filename: str):
    return resources.files("arvio") / "man" / filename


def _default_man_dir() -> Path:
    data_home = os.environ.get("XDG_DATA_HOME")
    base = Path(data_home) if data_home else Path.home() / ".local" / "share"
    return base / "man"


def _show_man(page: str) -> int:
    filename, _ = MAN_PAGES[page]
    with resources.as_file(_man_source(filename)) as path:
        if shutil.which("man") is None:
            print("arvio: the man command is not installed; the page is this file:")
            print(path)
            return 1
        return subprocess.run(["man", str(path)]).returncode


def _install_man(dest: Path) -> int:
    for filename, section in MAN_PAGES.values():
        target = dest / section / filename
        try:
            text = _man_source(filename).read_text(encoding="utf-8")
            target.parent.mkdir(parents=True, exist_ok=True)
            # atomic_write renames over the path, so a symlink there is replaced
            # and whatever it pointed at is left alone.
            atomic_write(target, text)
        except OSError as e:
            print(f"arvio: {e}", file=sys.stderr)
            return 1
        print(target)
    return 0


def cmd_man(args: argparse.Namespace) -> int:
    if args.dir and not args.install:
        print("arvio: --dir needs --install", file=sys.stderr)
        return 1
    if not args.install:
        return _show_man(args.page or "arvio")
    if args.page:
        print("arvio: --install copies both pages; leave PAGE out", file=sys.stderr)
        return 1
    return _install_man((args.dir or _default_man_dir()).expanduser())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arvio",
        description=(
            "Label eval items by hand, with the judge hidden until the end. "
            "With no command, start the labeling page for your inbox."
        ),
    )
    parser.add_argument("--version", action="version", version=f"arvio {__version__}")
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="print the page's link instead of opening it",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    p = sub.add_parser("add", help="check a task folder and add it to your inbox")
    p.add_argument("dir", type=Path, metavar="DIR")
    p.add_argument(
        "--import",
        dest="import_",
        action="store_true",
        help="copy the card and items into arvio's own tasks folder and add that copy",
    )
    p.add_argument(
        "--name", metavar="NAME", help="the copy's folder name (with --import)"
    )
    p.set_defaults(run=cmd_add)
    p = sub.add_parser("remove", help="take a task folder off your inbox")
    p.add_argument("dir", type=Path, metavar="DIR")
    p.set_defaults(run=cmd_remove)
    p = sub.add_parser("archive", help="move a task to the archived list")
    p.add_argument("target", metavar="NAME_OR_DIR")
    p.set_defaults(run=cmd_archive)
    p = sub.add_parser(
        "unarchive", help="move an archived task back to the active list"
    )
    p.add_argument("target", metavar="NAME_OR_DIR")
    p.set_defaults(run=cmd_unarchive)
    p = sub.add_parser("status", help="show what is waiting in your inbox")
    p.add_argument(
        "--archived", action="store_true", help="list archived tasks instead"
    )
    p.set_defaults(run=cmd_status)
    p = sub.add_parser("check", help="check a task folder; exits 1 on any error")
    p.add_argument("dir", type=Path, metavar="DIR")
    p.set_defaults(run=cmd_check)
    p = sub.add_parser(
        "report",
        help="compare your labels with the judge; exits 2 if the card's gate fails",
    )
    p.add_argument("dir", type=Path, metavar="DIR")
    p.add_argument("--by", metavar="META_KEY", help="also break it down by this key")
    p.add_argument("--json", action="store_true", help="print report.json instead")
    p.set_defaults(run=cmd_report)
    p = sub.add_parser(
        "labels",
        help="print the current label per item as JSON lines; exits 1 on any error",
    )
    p.add_argument("dir", type=Path, metavar="DIR")
    p.set_defaults(run=cmd_labels)
    p = sub.add_parser(
        "man", help="show arvio's man pages, or copy them where man looks"
    )
    p.add_argument(
        "page",
        nargs="?",
        choices=list(MAN_PAGES),
        help="arvio (the command, the default) or task (the task folder format)",
    )
    p.add_argument(
        "--install",
        action="store_true",
        help="copy both pages into a man folder, so plain `man arvio` works",
    )
    p.add_argument(
        "--dir",
        type=Path,
        metavar="DIR",
        help="the man folder to copy into (with --install); default ~/.local/share/man",
    )
    p.set_defaults(run=cmd_man)
    return parser


def cmd_serve(args: argparse.Namespace) -> int:
    tasks = read_registry()
    if tasks is None:
        print("arvio: the task registry can't be read; fix or move it", file=sys.stderr)
        return 1
    if not tasks:
        print("No tasks yet. Add one with: arvio add DIR")
        _print_guide()
        return 0
    return server.run(open_browser=not args.no_browser)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command is None:
        return cmd_serve(args)
    return args.run(args)
