import json

import pytest
from conftest import EXAMPLE, read_items, write_lines

from arvio.task import TaskError, check_task, load_task


def edit_card(folder, old, new):
    card = folder / "task.toml"
    text = card.read_text(encoding="utf-8")
    assert old in text, old
    card.write_text(text.replace(old, new, 1), encoding="utf-8")


def errors(folder):
    task, problems = check_task(folder)
    assert task is None
    return [str(p) for p in problems if not p.warning]


def warnings(folder):
    task, problems = check_task(folder)
    assert task is not None, [str(p) for p in problems]
    return [str(p) for p in problems if p.warning]


def label(task, item_id, answer="match", **extra):
    return {
        "kind": "label",
        "item_id": item_id,
        "task_version": task.card.version,
        "answer": answer,
        "item_hash": task.item(item_id).hash,
        **extra,
    }


def test_example_task_is_clean(task_dir):
    task, problems = check_task(task_dir)
    assert problems == []
    assert len(task.items) == 12
    assert [c.key for c in task.card.choices] == ["match", "mismatch", "cant_tell"]
    assert task.summary()["next_n"] == 10


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ('key = "cant_tell"', 'key = "unsure"', "a 'cant_tell' choice is required"),
        ('hotkey = "x"', 'hotkey = "m"', "'m' is already used by choice 1"),
        ('hotkey = "x"', 'hotkey = "n"', "'n' is kept for the note"),
        ('hotkey = "3"', 'hotkey = "u"', "'u' is already used by choice 3"),
        ("session_size = 10", "session_size = 10\ncolour = 1", "colour: unknown key"),
        ('kind = "single"', 'kind = "pick"', "kind: must be one of"),
        ('when = ["mismatch"]', 'when = ["nope"]', "'nope' is not a choice"),
        ('supported = ["match"]', 'supported = ["cant_tell"]', "can't be mapped"),
        (
            'supported = ["match"]',
            'supported = ["mismatch"]',
            "listed under both 'supported' and 'unsupported'",
        ),
        ("format = 1", "format = 2", "arvio reads format 1"),
        ("session_size = 10", "session_size = 0", "between 1 and 100"),
        ('kind = "edit"', 'kind = "text"', "only edit follow-ups take fields"),
        ("min_n = 10", "min_n = 10\nkappa = 0.6", "gate kappa: unknown key"),
    ],
)
def test_card_errors(task_dir, old, new, message):
    edit_card(task_dir, old, new)
    found = errors(task_dir)
    assert any(message in e for e in found), found
    assert all(e.startswith(str(task_dir / "task.toml")) for e in found)


def test_card_reports_several_errors_at_once(task_dir):
    edit_card(task_dir, 'hotkey = "x"', 'hotkey = "m"')
    edit_card(task_dir, "session_size = 10", "session_size = 500")
    assert len(errors(task_dir)) == 2


def test_toml_syntax_error(task_dir):
    edit_card(task_dir, 'title = "', "title = ")
    assert "cannot read" in errors(task_dir)[0]


def test_gate_needs_agreement(task_dir):
    card = task_dir / "task.toml"
    text = card.read_text(encoding="utf-8")
    start, end = text.index("[agreement]"), text.index("[gate]")
    card.write_text(text[:start] + text[end:], encoding="utf-8")
    assert any("needs an [agreement] section" in e for e in errors(task_dir))


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ('title = "Does the summary', 'title = "Is the summary'),
        ("purpose = ", 'purpose = "other" #'),
        ('example = "Notice', 'example = "A notice'),
        ('hotkey = "x"', 'hotkey = "z"'),
        ('label = "Matches"', 'label = "Yes"'),
        ("seconds_per_item = 30", "seconds_per_item = 45"),
    ],
)
def test_version_ignores_wording(task_dir, old, new):
    before = load_task(task_dir).card.version
    edit_card(task_dir, old, new)
    assert load_task(task_dir).card.version == before


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ('question = "Does', 'question = "Is it true that'),
        ('meaning = "Everything', 'meaning = "Nearly everything'),
        ('pin = "example-judge-v1"', 'pin = "example-judge-v2"'),
        ("required = true", "required = false"),
        ('meaning = "The notice says otherwise."', 'meaning = "Says otherwise."'),
    ],
)
def test_version_follows_the_question(task_dir, old, new):
    before = load_task(task_dir).card.version
    edit_card(task_dir, old, new)
    assert load_task(task_dir).card.version != before


def test_bad_json_line_is_named(task_dir):
    path = task_dir / "items.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[2] = lines[2][:-1]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    found = errors(task_dir)
    assert len(found) == 1
    assert found[0].startswith(f"{path}:3: error: not valid JSON")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda r: r.update(id=r["id"] + " x"), "id: use up to 200"),
        (lambda r: r.update(colour=1), "colour: unknown key"),
        (lambda r: r.update(display=[]), "display: list at least one block"),
        (lambda r: r["display"][0].update(kind="html"), "display 1 kind"),
        (lambda r: r["display"][0].update(highlight=["nowhere"]), "is not in the"),
        (lambda r: r["display"][2].update(fields=[["a"]]), "[label, value] pair"),
        (lambda r: r["display"][1].update(mono=True, extra=1), "extra: unknown"),
        (lambda r: r.update(judge={"verdict": "maybe"}), "maybe is not in the"),
        (lambda r: r.update(judge="pass"), "judge: must be an object or null"),
        (lambda r: r.update(prefill={"sentence": 3}), "prefill: values must"),
    ],
)
def test_item_errors(task_dir, change, message):
    rows = read_items(task_dir)
    change(rows[4])
    write_lines(task_dir / "items.jsonl", rows)
    found = errors(task_dir)
    assert any(":5: error: " in e and message in e for e in found), found


def test_duplicate_id_names_both_lines(task_dir):
    rows = read_items(task_dir)
    rows[6]["id"] = rows[1]["id"]
    write_lines(task_dir / "items.jsonl", rows)
    assert any(":7: " in e and "also on line 2" in e for e in errors(task_dir))


def test_items_without_judge_are_fine(task_dir):
    rows = read_items(task_dir)
    rows[0]["judge"] = None
    del rows[1]["judge"]
    rows[2]["judge"] = {"other": 1}
    write_lines(task_dir / "items.jsonl", rows)
    task = load_task(task_dir)
    assert [task.card.agreement.judge_class(i.judge) for i in task.items[:3]] == [
        None,
        None,
        None,
    ]


def test_non_string_judge_values_use_json_text(task_dir):
    edit_card(task_dir, "supported = [", '"true" = [')
    edit_card(task_dir, "unsupported = [", "false = [")
    rows = read_items(task_dir)
    for r in rows:
        r["judge"]["verdict"] = r["judge"]["verdict"] == "supported"
    write_lines(task_dir / "items.jsonl", rows)
    task = load_task(task_dir)
    assert task.card.agreement.judge_class(task.items[0].judge) == "true"


def test_missing_items_file(task_dir):
    (task_dir / "items.jsonl").unlink()
    assert errors(task_dir) == [f"{task_dir / 'items.jsonl'}: error: missing"]


def test_load_task_raises_with_every_error(task_dir):
    edit_card(task_dir, 'hotkey = "x"', 'hotkey = "m"')
    with pytest.raises(TaskError) as caught:
        load_task(task_dir)
    assert len(caught.value.problems) == 1


def test_latest_label_wins(task_dir):
    task = load_task(task_dir)
    a, b = task.items[0].id, task.items[1].id
    write_lines(
        task_dir / "labels.jsonl",
        [
            label(task, a, "match", seconds=10),
            label(task, b, "cant_tell", seconds=20),
            label(task, a, "mismatch", followups={"why": "contradicts"}, seconds=30),
        ],
    )
    task = load_task(task_dir)
    assert task.labels.current[a]["answer"] == "mismatch"
    assert len(task.waiting()) == 10
    assert task.labels.seconds == [10, 20, 30]
    assert task.pace() == 20


def test_reveal_locks_the_label(task_dir):
    task = load_task(task_dir)
    a = task.items[0].id
    reveal = {"kind": "reveal", "item_ids": [a], "task_version": task.card.version}
    write_lines(
        task_dir / "labels.jsonl",
        [label(task, a, "match"), reveal, label(task, a, "cant_tell")],
    )
    found = warnings(task_dir)
    assert any(":3: warning: " in w and "after its reveal" in w for w in found)
    task = load_task(task_dir)
    assert task.labels.current[a]["answer"] == "match"
    assert task.summary()["revealed"] == 1


def test_labels_under_an_older_card_are_counted_not_used(task_dir):
    task = load_task(task_dir)
    a = task.items[0].id
    write_lines(task_dir / "labels.jsonl", [label(task, a)])
    edit_card(task_dir, 'pin = "example-judge-v1"', 'pin = "example-judge-v2"')
    task = load_task(task_dir)
    assert task.labels.current == {}
    assert task.labels.older == {a}
    assert len(task.waiting()) == 12


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ({"item_id": "gone"}, "'gone' is not in items.jsonl"),
        ({"answer": "yes"}, "answer 'yes' is not a choice"),
        ({"answer": "mismatch"}, "'why' is required for 'mismatch'"),
        ({"followups": {"why": "contradicts"}}, "'why' does not apply to 'match'"),
        (
            {"answer": "mismatch", "followups": {"why": "nope"}},
            "must be one of its option keys",
        ),
        (
            {
                "answer": "mismatch",
                "followups": {"why": "contradicts", "fix": {"x": ""}},
            },
            "object of its field keys",
        ),
        ({"kind": "edit"}, "kind: must be label or reveal"),
        ({"seconds": "10"}, "seconds: must be a number"),
    ],
)
def test_label_errors(task_dir, row, message):
    task = load_task(task_dir)
    write_lines(task_dir / "labels.jsonl", [{**label(task, task.items[0].id), **row}])
    found = errors(task_dir)
    assert any(":1: error: " in e and message in e for e in found), found


def test_changed_item_warns(task_dir):
    task = load_task(task_dir)
    write_lines(task_dir / "labels.jsonl", [label(task, task.items[0].id)])
    rows = read_items(task_dir)
    rows[0]["display"][1]["text"] = "Something else."
    write_lines(task_dir / "items.jsonl", rows)
    assert any("changed after it was labeled" in w for w in warnings(task_dir))


def test_line_separator_inside_a_string_is_not_a_new_line(task_dir):
    rows = read_items(task_dir)
    rows[0]["display"][1]["text"] = "First part second part"
    path = task_dir / "items.jsonl"
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8",
    )
    assert " " in path.read_text(encoding="utf-8")
    assert len(load_task(task_dir).items) == 12


def test_the_spec_examples_pass_the_checker(tmp_path):
    spec = (EXAMPLE.parent.parent / "docs" / "format.md").read_text(encoding="utf-8")
    card = spec.split("```toml\n", 1)[1].split("```", 1)[0]
    item = spec.split("```json\n", 1)[1].split("```", 1)[0]
    (tmp_path / "task.toml").write_text(card, encoding="utf-8")
    (tmp_path / "items.jsonl").write_text(item, encoding="utf-8")
    task, problems = check_task(tmp_path)
    assert problems == []
    assert task.card.gate.agreement_lower == 0.7
