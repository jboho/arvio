import json
import os
import stat
import threading

import pytest

from arvio import store


def test_data_dir_follows_the_env_var(data_dir):
    assert store.data_dir() == data_dir


def test_atomic_write_replaces_and_leaves_no_temp_files(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("old", encoding="utf-8")
    store.atomic_write(path, "new", mode=0o600)
    assert path.read_text(encoding="utf-8") == "new"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert os.listdir(tmp_path) == ["state.json"]


def test_atomic_write_keeps_the_old_file_on_failure(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("old", encoding="utf-8")
    with pytest.raises(TypeError):
        store.atomic_write(path, 3)
    assert path.read_text(encoding="utf-8") == "old"
    assert os.listdir(tmp_path) == ["state.json"]


def test_append_jsonl_adds_one_line_per_row(tmp_path):
    path = tmp_path / "labels.jsonl"
    store.append_jsonl(path, {"a": 1})
    store.append_jsonl(path, {"b": "é"})
    assert path.read_text(encoding="utf-8") == '{"a": 1}\n{"b": "é"}\n'


def test_append_jsonl_moves_a_cut_line_aside(tmp_path):
    path = tmp_path / "labels.jsonl"
    path.write_text('{"a": 1}\n{"b":', encoding="utf-8")
    store.append_jsonl(path, {"c": 3})
    assert path.read_text(encoding="utf-8") == '{"a": 1}\n{"c": 3}\n'
    assert (tmp_path / "labels.jsonl.torn").read_text(encoding="utf-8") == '{"b":\n'


def test_append_jsonl_keeps_a_whole_line_that_lacks_its_newline(tmp_path):
    path = tmp_path / "labels.jsonl"
    path.write_text('{"a": 1}\n{"b": 2}', encoding="utf-8")
    store.append_jsonl(path, {"c": 3})
    assert path.read_text(encoding="utf-8") == '{"a": 1}\n{"b": 2}\n{"c": 3}\n'
    assert not (tmp_path / "labels.jsonl.torn").exists()


def test_register_is_idempotent_and_private(data_dir, tmp_path):
    assert store.register(tmp_path / "one") is True
    assert store.register(tmp_path / "one") is False
    assert store.register(tmp_path / "two") is True
    assert [t["dir"] for t in store.read_registry()] == [
        str(tmp_path / "one"),
        str(tmp_path / "two"),
    ]
    assert stat.S_IMODE(data_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE((data_dir / "tasks.json").stat().st_mode) == 0o600


def test_unregister(tmp_path):
    store.register(tmp_path / "one")
    assert store.unregister(tmp_path / "one") is True
    assert store.unregister(tmp_path / "one") is False
    assert store.read_registry() == []


def test_set_archived_toggles_and_reports_what_changed(tmp_path):
    store.register(tmp_path / "one")
    store.register(tmp_path / "two")
    assert store.set_archived(tmp_path / "one", True) is True
    assert store.set_archived(tmp_path / "one", True) is False
    assert store.set_archived(tmp_path / "nope", True) is None
    tasks = store.read_registry()
    assert tasks[0]["archived"] is True and "archived" not in tasks[1]
    assert store.set_archived(tmp_path / "one", False) is True
    assert store.set_archived(tmp_path / "one", False) is False
    assert "archived" not in store.read_registry()[0]


def test_a_registry_with_a_non_boolean_archived_reads_as_none(data_dir):
    data_dir.mkdir()
    bad = {"format": 1, "tasks": [{"dir": "/x", "archived": "yes"}]}
    (data_dir / "tasks.json").write_text(json.dumps(bad), encoding="utf-8")
    assert store.read_registry() is None


def test_missing_registry_is_empty_and_corrupt_registry_is_none(data_dir):
    assert store.read_registry() == []
    data_dir.mkdir()
    (data_dir / "tasks.json").write_text("{not json", encoding="utf-8")
    assert store.read_registry() is None
    (data_dir / "tasks.json").write_text(
        '{"format": 1, "tasks": [{"x": 1}]}', encoding="utf-8"
    )
    assert store.read_registry() is None


def test_registry_in_an_unknown_format_reads_as_none(data_dir):
    data_dir.mkdir()
    for text in ('{"tasks": []}', '{"format": 2, "tasks": []}'):
        (data_dir / "tasks.json").write_text(text, encoding="utf-8")
        assert store.read_registry() is None


def test_update_refuses_a_corrupt_registry(data_dir, tmp_path):
    data_dir.mkdir()
    (data_dir / "tasks.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(store.StoreError, match="can read"):
        store.register(tmp_path / "one")
    assert (data_dir / "tasks.json").read_text(encoding="utf-8") == "{not json"


def test_update_refuses_when_the_file_changed_underneath(data_dir, tmp_path):
    store.register(tmp_path / "one")
    registry = data_dir / "tasks.json"

    def hand_edit(tasks):
        registry.write_text(json.dumps({"tasks": []}) + "\n\n", encoding="utf-8")
        return tasks

    with pytest.raises(store.StoreError, match="changed while"):
        store.update_registry(hand_edit)
    assert json.loads(registry.read_text(encoding="utf-8")) == {"tasks": []}


def test_update_catches_a_hand_edit_of_the_same_size(data_dir, tmp_path):
    store.register(tmp_path / "one")
    registry = data_dir / "tasks.json"
    text = registry.read_text(encoding="utf-8")
    edited = text.replace(str(tmp_path / "one"), str(tmp_path / "won"))
    assert len(edited) == len(text)

    def hand_edit(tasks):
        registry.write_text(edited, encoding="utf-8")
        st = registry.stat()
        os.utime(registry, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
        return tasks

    with pytest.raises(store.StoreError, match="changed while"):
        store.update_registry(hand_edit)
    assert registry.read_text(encoding="utf-8") == edited


def test_update_waits_for_the_registry_lock(data_dir, tmp_path):
    data_dir.mkdir()
    worker = threading.Thread(target=store.register, args=(tmp_path / "one",))
    with store.locked(data_dir / ".lock"):
        worker.start()
        worker.join(timeout=0.3)
        assert worker.is_alive()
        assert not (data_dir / "tasks.json").exists()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert [t["dir"] for t in store.read_registry()] == [str(tmp_path / "one")]


def test_folder_lock_refuses_a_second_holder(tmp_path):
    # flock locks belong to each open() of the file, so a second open in the
    # same process is refused just as a second process would be.
    with store.locked(tmp_path, wait=False):
        with pytest.raises(store.StoreError, match="in use by another arvio"):
            with store.locked(tmp_path, wait=False):
                pass
    with store.locked(tmp_path, wait=False):
        pass
