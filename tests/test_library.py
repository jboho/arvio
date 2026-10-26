import pytest
from conftest import read_items, write_lines

from arvio import library
from arvio.cli import main
from arvio.store import locked, read_registry
from arvio.task import load_task


def label_row(task, index=0):
    return {
        "kind": "label",
        "item_id": task.items[index].id,
        "task_version": task.card.version,
        "answer": "match",
        "seconds": 12,
        "labeled_at": "2026-10-19T16:02:11Z",
    }


def registered():
    return [t["dir"] for t in read_registry()]


def test_import_copies_card_and_items_but_not_labels(task_dir, data_dir, capsys):
    write_lines(task_dir / "labels.jsonl", [label_row(load_task(task_dir))])
    assert main(["add", "--import", str(task_dir), "--name", "mine"]) == 0
    copy = data_dir / "tasks" / "mine"
    assert capsys.readouterr().out.startswith("imported: ")
    assert sorted(p.name for p in copy.iterdir()) == ["items.jsonl", "task.toml"]
    assert registered() == [str(copy.resolve())]


def test_import_again_keeps_the_labels_and_replaces_the_items(
    task_dir, data_dir, capsys
):
    main(["add", "--import", str(task_dir), "--name", "mine"])
    copy = data_dir / "tasks" / "mine"
    write_lines(copy / "labels.jsonl", [label_row(load_task(copy))])
    rows = read_items(task_dir)
    rows[5]["meta"] = {"note": "changed upstream"}
    write_lines(task_dir / "items.jsonl", rows)
    capsys.readouterr()
    assert main(["add", "--import", str(task_dir), "--name", "mine"]) == 0
    assert capsys.readouterr().out.startswith("updated: ")
    assert len((copy / "labels.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    assert "changed upstream" in (copy / "items.jsonl").read_text(encoding="utf-8")
    assert len(registered()) == 1


def test_import_refuses_to_drop_a_labeled_item(task_dir, data_dir, capsys):
    main(["add", "--import", str(task_dir), "--name", "mine"])
    copy = data_dir / "tasks" / "mine"
    task = load_task(copy)
    write_lines(copy / "labels.jsonl", [label_row(task)])
    before = (copy / "items.jsonl").read_text(encoding="utf-8")
    write_lines(task_dir / "items.jsonl", read_items(task_dir)[1:])
    capsys.readouterr()
    assert main(["add", "--import", str(task_dir), "--name", "mine"]) == 1
    assert "not imported" in capsys.readouterr().err
    assert (copy / "items.jsonl").read_text(encoding="utf-8") == before
    assert not [p for p in (data_dir / "tasks").iterdir() if p.name.startswith(".")]


def test_import_refuses_a_broken_task_and_leaves_no_copy(task_dir, data_dir, capsys):
    rows = read_items(task_dir)
    rows[0]["display"][0]["kind"] = "html"
    write_lines(task_dir / "items.jsonl", rows)
    assert main(["add", "--import", str(task_dir), "--name", "mine"]) == 1
    assert "items.jsonl:1: error" in capsys.readouterr().err
    assert not (data_dir / "tasks" / "mine").exists()


@pytest.mark.parametrize("name", ["", "Mine", "../x", "a b", ".hidden"])
def test_import_rejects_unusable_names(task_dir, data_dir, name, capsys):
    assert main(["add", "--import", str(task_dir), "--name", name]) == 1
    assert "not a usable task name" in capsys.readouterr().err
    assert not (data_dir / "tasks").exists() or not list((data_dir / "tasks").iterdir())


def test_import_names_the_copy_after_the_folder_by_default(task_dir, data_dir):
    assert main(["add", "--import", str(task_dir)]) == 0
    assert (data_dir / "tasks" / "task").is_dir()


def test_name_needs_import(task_dir, capsys):
    assert main(["add", str(task_dir), "--name", "mine"]) == 1
    assert "--name needs --import" in capsys.readouterr().err


def test_import_refuses_the_library_copy_itself(task_dir, data_dir, capsys):
    main(["add", "--import", str(task_dir), "--name", "mine"])
    capsys.readouterr()
    assert main(["add", "--import", str(data_dir / "tasks" / "mine")]) == 1
    assert "already arvio's copy" in capsys.readouterr().err


def test_archive_moves_the_task_with_its_labels_and_leaves_the_inbox(
    task_dir, data_dir, capsys
):
    main(["add", "--import", str(task_dir), "--name", "mine"])
    copy = data_dir / "tasks" / "mine"
    write_lines(copy / "labels.jsonl", [label_row(load_task(copy))])
    assert main(["archive", "mine"]) == 0
    assert not copy.exists()
    assert (data_dir / "archive" / "mine" / "labels.jsonl").is_file()
    assert registered() == []
    assert str(data_dir / "archive" / "mine") in capsys.readouterr().out


def test_archive_refuses_a_name_that_is_not_in_the_library(task_dir, data_dir, capsys):
    main(["add", str(task_dir)])
    assert main(["archive", "task"]) == 1
    assert "no task named" in capsys.readouterr().err
    assert task_dir.is_dir()
    assert registered() == [str(task_dir)]


def test_archive_refuses_to_overwrite(task_dir, data_dir, capsys):
    main(["add", "--import", str(task_dir), "--name", "mine"])
    (data_dir / "archive" / "mine").mkdir(parents=True)
    assert main(["archive", "mine"]) == 1
    assert "already exists" in capsys.readouterr().err
    assert (data_dir / "tasks" / "mine").is_dir()
    assert len(registered()) == 1


def test_archive_refuses_while_the_page_has_the_folder_open(task_dir, data_dir, capsys):
    main(["add", "--import", str(task_dir), "--name", "mine"])
    copy = data_dir / "tasks" / "mine"
    with locked(copy):
        assert main(["archive", "mine"]) == 1
    assert "in use by another arvio" in capsys.readouterr().err
    assert copy.is_dir()
    assert len(registered()) == 1


def test_import_refuses_a_name_already_in_the_archive(task_dir, data_dir, capsys):
    main(["add", "--import", str(task_dir), "--name", "mine"])
    main(["archive", "mine"])
    capsys.readouterr()
    assert main(["add", "--import", str(task_dir), "--name", "mine"]) == 1
    assert "already in the archive" in capsys.readouterr().err


def test_library_error_carries_problems(task_dir):
    write_lines(task_dir / "items.jsonl", [{"id": "x"}])
    with pytest.raises(library.LibraryError) as e:
        library.import_task(task_dir, "mine")
    assert e.value.problems
