import json
import shutil
from pathlib import Path

import pytest

EXAMPLE = Path(__file__).parent.parent / "examples" / "hello"


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    """Every test gets its own registry folder, never the real ~/.arvio."""
    folder = tmp_path / "arvio-data"
    monkeypatch.setenv("ARVIO_DATA_DIR", str(folder))
    return folder


@pytest.fixture
def task_dir(tmp_path):
    """A copy of the example task in a temp folder."""
    folder = tmp_path / "task"
    shutil.copytree(EXAMPLE, folder, ignore=shutil.ignore_patterns("*.py"))
    return folder


def write_lines(path: Path, rows: list) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def finish_task(folder: Path, reveal: bool = True) -> None:
    """Label every item; unless told not to, lock the labels with a reveal."""
    from arvio.task import load_task

    task = load_task(folder)
    rows = [
        {
            "kind": "label",
            "item_id": i.id,
            "item_hash": i.hash,
            "task_version": task.card.version,
            "answer": "match",
            "followups": {},
            "seconds": 10,
        }
        for i in task.items
    ]
    if reveal:
        rows.append(
            {
                "kind": "reveal",
                "item_ids": [i.id for i in task.items],
                "task_version": task.card.version,
            }
        )
    write_lines(folder / "labels.jsonl", rows)


def read_items(folder: Path) -> list[dict]:
    lines = (folder / "items.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]
