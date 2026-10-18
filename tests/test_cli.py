import json

from conftest import read_items, write_lines

from arvio.cli import main
from arvio.task import load_task


def test_no_command_prints_help(capsys):
    assert main([]) == 0
    assert "add" in capsys.readouterr().out


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


def test_remove(task_dir, capsys):
    main(["add", str(task_dir)])
    assert main(["remove", str(task_dir)]) == 0
    assert main(["remove", str(task_dir)]) == 1
    assert "not in your inbox" in capsys.readouterr().out
