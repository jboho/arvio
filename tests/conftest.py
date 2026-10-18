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


def read_items(folder: Path) -> list[dict]:
    lines = (folder / "items.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]
