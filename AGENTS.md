# arvio: agent guide

arvio shows eval items to a person one at a time, with the question and the meaning of each answer on screen, and keeps the judge's answer hidden until the session ends. Projects talk to it only through files: a task folder holds `task.toml`, `items.jsonl` and `labels.jsonl`. The format is specified in `docs/format.md`; when this guide and the code disagree, the code wins.

## Layout

- `src/arvio/task.py`: reads and checks a task folder (card, items, label log). Every reader fails closed and names the file, plus the line in the JSONL files or the setting in `task.toml`.
- `src/arvio/store.py`: atomic writes, the fsync'd label append, flock, and the inbox registry (`$ARVIO_DATA_DIR/tasks.json`, default `~/.arvio`).
- `src/arvio/stats.py`: Wilson range, multi-class Cohen's kappa, seeded bootstrap.
- `src/arvio/cli.py`: the `arvio` command (`add`, `remove`, `status`, `check`).
- `examples/hello/`: a made-up task that uses every part of the card. `make_items.py` regenerates its `items.jsonl` the way a project's exporter would.
- `tests/`: pytest.

## Commands

| What | Command |
| --- | --- |
| Set up | `uv sync` |
| Lint | `uv run ruff check . && uv run ruff format --check .` |
| Test | `uv run pytest -q` |
| Try it | `uv run arvio check examples/hello` |

CI (`.github/workflows/test.yml`) runs the same lint and tests on Python 3.11, 3.12 and 3.13.

## Rules

- Python standard library only at runtime. Dev tools (ruff, pytest) live in the uv `dev` group, and `uv.lock` is committed.
- Write whole files with `store.atomic_write` (temp file, fsync, rename) and the label log with `store.append_jsonl` (append, fsync). A reader returns None when it fails and `[]` only when there is truly nothing.
- Before writing a file other processes also write (the registry), re-check its mtime and size, and refuse if it changed.
- Tests run under pytest's temp folders with `ARVIO_DATA_DIR` set by an autouse fixture. Never point a test at `~/.arvio` or a real project's task folder.
- The judge answer and `meta` never reach the page before the session's reveal. Any code that sends item data to the browser must build it from an allow-list of keys.
- A change to the file format updates `docs/format.md` in the same PR. A change that old files can't satisfy bumps `FORMAT` in `task.py`.
- No project data in this repo: examples are made up.
- The version lives only in `pyproject.toml`. There is no CHANGELOG. A release is a PR titled `chore: release X.Y.Z`, then an annotated tag `vX.Y.Z` on the merge commit.
- Conventional commits with a scope (`feat(task): ...`), squash merges.
