import json

import pytest
from conftest import read_items, write_lines

from arvio import server
from arvio.cli import main
from arvio.task import load_task


def test_no_command_with_an_empty_inbox_says_how_to_add(capsys, monkeypatch):
    monkeypatch.setattr(server, "run", lambda **kw: pytest.fail("served"))
    assert main([]) == 0
    assert "Add one with: arvio add DIR" in capsys.readouterr().out


def test_no_command_serves_the_inbox(task_dir, monkeypatch):
    calls = []
    monkeypatch.setattr(server, "run", lambda **kw: calls.append(kw) or 0)
    main(["add", str(task_dir)])
    assert main(["--no-browser"]) == 0
    assert main([]) == 0
    assert calls == [{"open_browser": False}, {"open_browser": True}]


def test_no_command_with_a_broken_registry_fails(data_dir, capsys, monkeypatch):
    monkeypatch.setattr(server, "run", lambda **kw: pytest.fail("served"))
    data_dir.mkdir()
    (data_dir / "tasks.json").write_text("{", encoding="utf-8")
    assert main([]) == 1
    assert "registry can't be read" in capsys.readouterr().err


def test_check_ok(task_dir, capsys):
    assert main(["check", str(task_dir)]) == 0
    assert capsys.readouterr().out.startswith("ok: 12 items, 0 labeled")


def test_check_fails_closed_with_file_and_line(task_dir, capsys):
    rows = read_items(task_dir)
    rows[3]["display"][0]["kind"] = "html"
    write_lines(task_dir / "items.jsonl", rows)
    assert main(["check", str(task_dir)]) == 1
    out = capsys.readouterr()
    assert f"{task_dir / 'items.jsonl'}:4: error: display 1 kind" in out.err
    assert "1 error;" in out.out


def test_check_on_a_missing_folder(tmp_path, capsys):
    assert main(["check", str(tmp_path / "nope")]) == 1
    assert "not a folder" in capsys.readouterr().err


def test_add_then_status(task_dir, data_dir, capsys):
    task = load_task(task_dir)
    write_lines(
        task_dir / "labels.jsonl",
        [
            {
                "kind": "label",
                "item_id": task.items[0].id,
                "task_version": task.card.version,
                "answer": "match",
                "seconds": 12,
                "labeled_at": "2026-10-19T16:02:11Z",
            }
        ],
    )
    assert main(["add", str(task_dir)]) == 0
    assert "added: Does the summary match the notice? (11 waiting)" in (
        capsys.readouterr().out
    )
    assert main(["add", str(task_dir)]) == 0
    assert "already in your inbox" in capsys.readouterr().out
    registry = json.loads((data_dir / "tasks.json").read_text(encoding="utf-8"))
    assert [t["dir"] for t in registry["tasks"]] == [str(task_dir)]

    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "waiting 11, labeled 1 (judge shown for 0)" in out
    assert "last label 2026-10-19" in out
    assert "next session: 10 items, about 5 min" in out


def test_status_without_a_judge_counts_locked_labels(task_dir, capsys):
    card = task_dir / "task.toml"
    text = card.read_text(encoding="utf-8")
    card.write_text(text[: text.index("[agreement]")], encoding="utf-8")
    task = load_task(task_dir)
    write_lines(
        task_dir / "labels.jsonl",
        [
            {
                "kind": "label",
                "item_id": task.items[0].id,
                "task_version": task.card.version,
                "answer": "match",
                "seconds": 12,
                "labeled_at": "2026-10-19T16:02:11Z",
            }
        ],
    )
    main(["add", str(task_dir)])
    capsys.readouterr()
    assert main(["status"]) == 0
    assert "waiting 11, labeled 1 (0 locked)" in capsys.readouterr().out


def test_add_refuses_a_broken_task(task_dir, data_dir, capsys):
    (task_dir / "task.toml").write_text('format = 1\ntitle = "x"\n', encoding="utf-8")
    assert main(["add", str(task_dir)]) == 1
    assert "not added" in capsys.readouterr().out
    assert not (data_dir / "tasks.json").exists()


def test_status_flags_a_task_that_broke_after_adding(task_dir, capsys):
    main(["add", str(task_dir)])
    (task_dir / "items.jsonl").unlink()
    capsys.readouterr()
    assert main(["status"]) == 1
    out = capsys.readouterr().out
    assert "can't open: missing" in out
    assert f"see: arvio check {task_dir}" in out


def test_status_with_an_empty_inbox(capsys):
    assert main(["status"]) == 0
    assert "No tasks yet" in capsys.readouterr().out


def test_add_reports_a_data_folder_it_cannot_write(
    task_dir, tmp_path, monkeypatch, capsys
):
    blocker = tmp_path / "a-file"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("ARVIO_DATA_DIR", str(blocker / "data"))
    assert main(["add", str(task_dir)]) == 1
    assert main(["remove", str(task_dir)]) == 1
    err = capsys.readouterr().err
    assert err.count("arvio: ") == 2
    assert "Traceback" not in err


def test_remove(task_dir, capsys):
    main(["add", str(task_dir)])
    assert main(["remove", str(task_dir)]) == 0
    assert main(["remove", str(task_dir)]) == 1
    assert "not in your inbox" in capsys.readouterr().out


def test_labels_prints_the_current_label_per_item(task_dir, capsys):
    task = load_task(task_dir)
    first, second, third = (item.id for item in task.items[:3])

    def label(item_id, answer, version=task.card.version, **extra):
        return {
            "kind": "label",
            "item_id": item_id,
            "task_version": version,
            "answer": answer,
            **extra,
        }

    write_lines(
        task_dir / "labels.jsonl",
        [
            label(second, "cant_tell"),
            label(first, "cant_tell"),
            label(first, "match", labeler="sam"),
            {
                "kind": "reveal",
                "item_ids": [first],
                "task_version": task.card.version,
            },
            label(third, "match", version="0ldversion00"),
        ],
    )
    assert main(["labels", str(task_dir)]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows == [
        {
            "item_id": first,
            "task_version": task.card.version,
            "answer": "match",
            "labeler": "sam",
            "locked": True,
        },
        {
            "item_id": second,
            "task_version": task.card.version,
            "answer": "cant_tell",
            "locked": False,
        },
    ]


def test_labels_with_none_yet_prints_nothing(task_dir, capsys):
    assert main(["labels", str(task_dir)]) == 0
    assert capsys.readouterr().out == ""


def test_labels_fails_closed(task_dir, capsys):
    item_id = read_items(task_dir)[0]["id"]
    write_lines(
        task_dir / "labels.jsonl",
        [
            {
                "kind": "label",
                "item_id": item_id,
                "task_version": load_task(task_dir).card.version,
                "answer": "match",
            },
            {"kind": "label", "item_id": item_id},
        ],
    )
    assert main(["labels", str(task_dir)]) == 1
    out = capsys.readouterr()
    assert out.out == ""
    assert "labels.jsonl:2" in out.err and "no labels" in out.err
