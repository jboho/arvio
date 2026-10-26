import json
import shutil
import subprocess
from importlib import resources

import pytest
from conftest import finish_task, read_items, write_lines

from arvio import server
from arvio.cli import main
from arvio.store import read_registry
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


def test_archive_and_unarchive_move_a_task_between_the_lists(
    task_dir, data_dir, capsys
):
    finish_task(task_dir)
    main(["add", str(task_dir)])
    capsys.readouterr()
    assert main(["archive", str(task_dir)]) == 0
    assert capsys.readouterr().out.startswith("moved to archived: ")
    assert main(["archive", str(task_dir)]) == 0
    assert capsys.readouterr().out.startswith("already archived: ")
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "No active tasks." in out and "1 archived; list them with" in out
    assert main(["status", "--archived"]) == 0
    out = capsys.readouterr().out
    assert "waiting 0, labeled 12" in out
    assert main(["unarchive", str(task_dir)]) == 0
    assert capsys.readouterr().out.startswith("moved to active: ")
    assert main(["status"]) == 0
    assert "waiting 0, labeled 12" in capsys.readouterr().out
    assert (task_dir / "task.toml").is_file()


def test_archive_refuses_a_task_that_is_not_finished(task_dir, capsys):
    main(["add", str(task_dir)])
    capsys.readouterr()
    assert main(["archive", str(task_dir)]) == 1
    assert "12 items are still waiting" in capsys.readouterr().err
    finish_task(task_dir, reveal=False)
    assert main(["archive", str(task_dir)]) == 1
    assert "not locked yet" in capsys.readouterr().err
    finish_task(task_dir)
    assert main(["archive", str(task_dir)]) == 0
    assert read_registry()[0]["archived"] is True


def test_archive_allows_a_folder_that_is_gone(task_dir, capsys):
    main(["add", str(task_dir)])
    shutil.rmtree(task_dir)
    capsys.readouterr()
    assert main(["archive", str(task_dir)]) == 0
    assert read_registry()[0]["archived"] is True


def test_archive_refuses_a_task_that_is_not_in_the_inbox(task_dir, capsys):
    assert main(["archive", str(task_dir)]) == 1
    assert "not in your inbox" in capsys.readouterr().err


def test_serving_still_works_when_every_task_is_archived(task_dir, monkeypatch):
    calls = []
    monkeypatch.setattr(server, "run", lambda **kw: calls.append(kw) or 0)
    finish_task(task_dir)
    main(["add", str(task_dir)])
    assert main(["archive", str(task_dir)]) == 0
    assert main(["--no-browser"]) == 0
    assert calls == [{"open_browser": False}]


PAGE_FILES = {"man1/arvio.1": "arvio.1", "man5/arvio-task.5": "arvio-task.5"}


def packaged(filename):
    return (resources.files("arvio") / "man" / filename).read_text(encoding="utf-8")


def test_the_man_pages_ship_inside_the_package():
    for filename in PAGE_FILES.values():
        assert packaged(filename).startswith(".TH ")


def test_man_install_writes_both_pages_and_prints_their_paths(tmp_path, capsys):
    dest = tmp_path / "share" / "man"
    assert main(["man", "--install", "--dir", str(dest)]) == 0
    for where, filename in PAGE_FILES.items():
        assert (dest / where).read_text(encoding="utf-8") == packaged(filename)
    assert capsys.readouterr().out.splitlines() == [
        str(dest / where) for where in PAGE_FILES
    ]


def test_man_install_again_overwrites_the_pages(tmp_path):
    dest = tmp_path / "man"
    main(["man", "--install", "--dir", str(dest)])
    (dest / "man1" / "arvio.1").write_text("stale", encoding="utf-8")
    assert main(["man", "--install", "--dir", str(dest)]) == 0
    assert (dest / "man1" / "arvio.1").read_text(encoding="utf-8") == packaged(
        "arvio.1"
    )


def test_man_install_replaces_a_symlink_and_leaves_its_target(tmp_path):
    dest = tmp_path / "man"
    (dest / "man1").mkdir(parents=True)
    target = tmp_path / "clone-copy.1"
    target.write_text("the clone's page", encoding="utf-8")
    link = dest / "man1" / "arvio.1"
    link.symlink_to(target)
    assert main(["man", "--install", "--dir", str(dest)]) == 0
    assert not link.is_symlink()
    assert link.read_text(encoding="utf-8") == packaged("arvio.1")
    assert target.read_text(encoding="utf-8") == "the clone's page"


def test_man_install_defaults_to_xdg_data_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert main(["man", "--install"]) == 0
    assert (tmp_path / "xdg" / "man" / "man1" / "arvio.1").is_file()


def test_man_install_defaults_to_local_share_under_home(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert main(["man", "--install"]) == 0
    page = tmp_path / "home" / ".local" / "share" / "man" / "man5" / "arvio-task.5"
    assert page.is_file()


def test_man_install_reports_a_folder_it_cannot_create(tmp_path, capsys):
    blocker = tmp_path / "file"
    blocker.write_text("", encoding="utf-8")
    assert main(["man", "--install", "--dir", str(blocker / "man")]) == 1
    assert capsys.readouterr().err.startswith("arvio: ")


def test_man_dir_without_install_is_an_error(tmp_path, capsys):
    assert main(["man", "--dir", str(tmp_path)]) == 1
    assert "--dir needs --install" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_man_install_with_a_page_is_an_error(tmp_path, capsys):
    assert main(["man", "task", "--install", "--dir", str(tmp_path / "m")]) == 1
    assert "leave PAGE out" in capsys.readouterr().err
    assert not (tmp_path / "m").exists()


def test_man_shows_the_packaged_page_and_passes_the_status_through(monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 3)

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/man")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert main(["man"]) == 3
    assert main(["man", "task"]) == 3
    assert [(c[0], c[1].rsplit("/", 1)[1]) for c in calls] == [
        ("man", "arvio.1"),
        ("man", "arvio-task.5"),
    ]


def test_man_without_a_man_command_prints_the_path(monkeypatch, capsys):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("ran man"))
    assert main(["man", "task"]) == 1
    out = capsys.readouterr().out.splitlines()
    assert out[-1].endswith("/arvio/man/arvio-task.5")
