"""The arvio command."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from arvio import __version__
from arvio.store import StoreError, read_registry, register, unregister
from arvio.task import Problem, check_task


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
        return 1
    s = task.summary()
    print(
        f"ok: {s['items']} items, {s['labeled']} labeled, "
        f"card version {task.card.version}"
    )
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    task, problems = check_task(args.dir)
    _print_problems(problems)
    if task is None:
        print("not added: fix the errors above first")
        return 1
    try:
        added = register(task.dir)
    except StoreError as e:
        print(f"arvio: {e}", file=sys.stderr)
        return 1
    state = "added" if added else "already in your inbox"
    print(f"{state}: {task.card.title} ({len(task.waiting())} waiting)")
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    folder = Path(args.dir).expanduser().resolve()
    try:
        removed = unregister(folder)
    except StoreError as e:
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
        return 0
    broken = 0
    for entry in tasks:
        task, problems = check_task(Path(entry["dir"]))
        if task is None:
            broken += 1
            first = next((p for p in problems if not p.warning), None)
            print(f"{entry['dir']}\n  can't open: {first.message if first else '?'}")
            print(f"  see: arvio check {entry['dir']}\n")
            continue
        s = task.summary()
        line = f"  waiting {s['waiting']}, labeled {s['labeled']}"
        if s["labeled"]:
            line += f" (judge shown for {s['revealed']})"
        if s["older"]:
            line += f", {s['older']} labeled under an older card"
        if s["last_at"]:
            line += f", last label {s['last_at'][:10]}"
        print(f"{s['title']}\n  {task.dir}\n{line}")
        if s["next_n"]:
            print(f"  next session: {s['next_n']} items, about {s['next_minutes']} min")
        print()
    return 1 if broken else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arvio",
        description="Label eval items by hand, with the judge hidden until the end.",
    )
    parser.add_argument("--version", action="version", version=f"arvio {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    p = sub.add_parser("add", help="check a task folder and add it to your inbox")
    p.add_argument("dir", type=Path, metavar="DIR")
    p.set_defaults(run=cmd_add)
    p = sub.add_parser("remove", help="take a task folder off your inbox")
    p.add_argument("dir", type=Path, metavar="DIR")
    p.set_defaults(run=cmd_remove)
    p = sub.add_parser("status", help="show what is waiting in your inbox")
    p.set_defaults(run=cmd_status)
    p = sub.add_parser("check", help="check a task folder; exits 1 on any error")
    p.add_argument("dir", type=Path, metavar="DIR")
    p.set_defaults(run=cmd_check)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    return args.run(args)
