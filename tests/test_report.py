import json
import os

import pytest
from conftest import read_items, write_lines

from arvio import report
from arvio.cli import main
from arvio.stats import wilson
from arvio.task import load_task

OPPOSITE = {"match": "mismatch", "mismatch": "match"}


def agreeing(judge):
    return "match" if judge["verdict"] == "supported" else "mismatch"


def write_labels(folder, answers):
    """answers: item id -> answer, or (answer, followups)."""
    task = load_task(folder)
    rows = []
    for item_id, value in answers.items():
        answer, followups = value if isinstance(value, tuple) else (value, None)
        if followups is None:
            followups = {"why": "contradicts"} if answer == "mismatch" else {}
        rows.append(
            {
                "kind": "label",
                "item_id": item_id,
                "item_hash": task.item(item_id).hash,
                "task_version": task.card.version,
                "answer": answer,
                "followups": followups,
                "seconds": 10,
            }
        )
    write_lines(folder / "labels.jsonl", rows)
    return load_task(folder)


def judges(folder):
    return {row["id"]: row["judge"] for row in read_items(folder)}


def all_agree(folder):
    out = {}
    for item_id, judge in judges(folder).items():
        answer = agreeing(judge)
        why = {"why": judge["reason"]} if answer == "mismatch" else {}
        out[item_id] = (answer, why)
    return out


def edit_card(folder, old, new):
    card = folder / "task.toml"
    text = card.read_text(encoding="utf-8")
    assert old in text
    card.write_text(text.replace(old, new, 1), encoding="utf-8")


def test_no_labels_yet(task_dir):
    r = report.build(load_task(task_dir))
    o = r["overall"]
    assert (r["items"], r["labeled"], o["n"], o["cant_tell_rate"]) == (12, 0, 0, None)
    a = o["agreement"]
    assert (a["compared"], a["rate"], a["range"], a["kappa"]) == (0, None, [0, 1], None)
    assert r["gate"]["enough"] is False and r["gate"]["passed"] is False
    text = report.markdown(load_task(task_dir), r)
    assert "No labels to compare with the judge yet." in text
    assert "Result: **not yet: 0 of 10 compared**." in text


def test_full_agreement_passes_the_gate(task_dir):
    task = write_labels(task_dir, all_agree(task_dir))
    r = report.build(task)
    a = r["overall"]["agreement"]
    assert (a["compared"], a["agreed"], a["rate"]) == (12, 12, 1.0)
    assert a["range"] == pytest.approx(list(wilson(12, 12)))
    assert a["kappa"] == pytest.approx(1.0)
    assert a["kappa_range"] == pytest.approx([1.0, 1.0])
    assert a["judge_values"] == ["supported", "unsupported"]
    assert a["confusion"] == {
        "supported": {"match": 7, "mismatch": 0},
        "unsupported": {"match": 0, "mismatch": 5},
    }
    [why] = a["compare"]
    assert (why["followup"], why["compared"], why["agreed"]) == ("why", 5, 5)
    assert r["overall"]["choices"] == {"match": 7, "mismatch": 5, "cant_tell": 0}
    gate = r["gate"]
    assert gate["enough"] and gate["passed"]
    assert [c["check"] for c in gate["checks"]] == ["agreement_lower", "kappa_lower"]
    assert "Result: **passed**." in report.markdown(task, r)


def test_full_disagreement_fails_the_gate(task_dir):
    answers = {i: OPPOSITE[agreeing(j)] for i, j in judges(task_dir).items()}
    task = write_labels(task_dir, answers)
    r = report.build(task)
    a = r["overall"]["agreement"]
    assert (a["compared"], a["agreed"]) == (12, 0)
    assert a["kappa"] < 0
    assert r["gate"]["enough"] and not r["gate"]["passed"]
    assert "Result: **failed**." in report.markdown(task, r)


def test_kappa_is_not_defined_when_the_judge_used_one_answer(task_dir):
    answers = {
        i: agreeing(j) for i, j in judges(task_dir).items() if agreeing(j) == "match"
    }
    task = write_labels(task_dir, answers)
    r = report.build(task)
    a = r["overall"]["agreement"]
    assert a["judge_values"] == ["supported"]
    assert (a["kappa"], a["kappa_range"], a["kappa_resamples"]) == (None, None, 0)
    kappa_check = r["gate"]["checks"][1]
    assert kappa_check["got"] is None and kappa_check["passed"] is False
    assert "Kappa: not defined" in report.markdown(task, r)


def test_cant_tell_and_no_judge_are_counted_not_compared(task_dir):
    rows = read_items(task_dir)
    rows[0]["judge"] = None
    write_lines(task_dir / "items.jsonl", rows)
    ids = [row["id"] for row in rows]
    task = write_labels(
        task_dir, {ids[0]: "match", ids[1]: "cant_tell", ids[2]: "match"}
    )
    o = report.build(task)["overall"]
    assert o["n"] == 3
    assert o["cant_tell_rate"] == pytest.approx(1 / 3)
    assert o["agreement"]["compared"] == 1
    assert o["agreement"]["no_judge"] == 1
    text = report.markdown(task, report.build(task))
    assert "Labels on items with no judge answer: 1." in text
    assert "Can't tell: 33% of labels." in text


def test_a_choice_in_no_map_list_never_agrees(task_dir):
    edit_card(
        task_dir,
        "[[followups]]",
        '[[choices]]\nkey = "partly"\nhotkey = "p"\nlabel = "Partly"\n'
        'meaning = "Some of it."\n\n[[followups]]',
    )
    ids = list(judges(task_dir))
    task = write_labels(task_dir, {i: "partly" for i in ids})
    a = report.build(task)["overall"]["agreement"]
    assert (a["compared"], a["agreed"]) == (12, 0)
    assert a["confusion"]["supported"]["partly"] == 7


def test_by_a_meta_key(task_dir):
    rows = read_items(task_dir)
    del rows[0]["meta"]["line"]
    write_lines(task_dir / "items.jsonl", rows)
    task = write_labels(task_dir, all_agree(task_dir))
    r = report.build(task, by="line")
    assert [(b["value"], b["n"]) for b in r["buckets"]] == [
        ("(none)", 1),
        ("north", 5),
        ("south", 6),
    ]
    assert all(b["agreement"]["agreed"] == b["n"] for b in r["buckets"])
    assert "## By line" in report.markdown(task, r)


def test_a_card_without_agreement_reports_counts_only(task_dir):
    card = task_dir / "task.toml"
    text = card.read_text(encoding="utf-8")
    card.write_text(text[: text.index("[agreement]")], encoding="utf-8")
    task = write_labels(task_dir, {list(judges(task_dir))[0]: "match"})
    r = report.build(task)
    assert "agreement" not in r["overall"] and r["gate"] is None
    assert "Against the judge" not in report.markdown(task, r)
    assert main(["report", str(task_dir)]) == 0


def test_table_cells_escape_pipes_and_newlines(task_dir):
    edit_card(task_dir, 'label = "Matches"', 'label = "Matches | fits"')
    text = report.markdown(load_task(task_dir), report.build(load_task(task_dir)))
    assert "| Matches \\| fits | 0 |" in text


def test_write_leaves_both_files_and_no_temp_files(task_dir):
    task = write_labels(task_dir, all_agree(task_dir))
    before = set(os.listdir(task_dir))
    r = report.write(task, by="line")
    assert set(os.listdir(task_dir)) - before == {"report.md", "report.json"}
    saved = json.loads((task_dir / "report.json").read_text(encoding="utf-8"))
    assert saved == json.loads(json.dumps(r))
    assert saved["format"] == report.REPORT_FORMAT
    md = (task_dir / "report.md").read_text(encoding="utf-8")
    assert md == report.markdown(task, r)


def test_cli_report_exit_codes(task_dir, capsys):
    assert main(["report", str(task_dir)]) == 2
    write_labels(task_dir, all_agree(task_dir))
    assert main(["report", str(task_dir), "--by", "line"]) == 0
    out = capsys.readouterr().out
    assert "Result: **passed**." in out and "## By line" in out
    assert f"wrote {task_dir / 'report.md'}" in out
    (task_dir / "items.jsonl").write_text("{broken\n", encoding="utf-8")
    assert main(["report", str(task_dir)]) == 1
    assert "no report" in capsys.readouterr().err


def test_cli_report_json(task_dir, capsys):
    write_labels(task_dir, all_agree(task_dir))
    assert main(["report", str(task_dir), "--json"]) == 0
    printed = json.loads(capsys.readouterr().out)
    saved = json.loads((task_dir / "report.json").read_text(encoding="utf-8"))
    assert printed == saved
