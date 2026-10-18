"""Agreement between the labeler and the judge, overall and per bucket.

Only current labels count: the latest label per item under the current card.
A "can't tell" answer and an item without a judge answer are counted but never
compared.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from arvio import store
from arvio.stats import bootstrap_kappa, kappa, wilson
from arvio.task import CANT_TELL, Card, Task, judge_key

REPORT_FORMAT = 1
NO_VALUE = "(none)"


def outcome(card: Card, answer: str, judge: Any) -> bool | None:
    """Whether one answer agrees with the judge; None when they can't be compared."""
    a = card.agreement
    if a is None or answer == CANT_TELL:
        return None
    judge_class = a.judge_class(judge)
    if judge_class is None:
        return None
    return a.human_class(answer) == judge_class


def _same(kind: str, mine: Any, theirs: Any) -> bool:
    if kind == "text":
        return str(mine).strip().casefold() == str(theirs).strip().casefold()
    return mine == theirs


def bucket(card: Card, rows: list[tuple[Any, dict]]) -> dict:
    """Counts and agreement for (judge answer, label row) pairs."""
    n = len(rows)
    counts = Counter(label["answer"] for _, label in rows)
    out: dict[str, Any] = {
        "n": n,
        "choices": {c.key: counts.get(c.key, 0) for c in card.choices},
        "cant_tell_rate": counts.get(CANT_TELL, 0) / n if n else None,
    }
    a = card.agreement
    if a is None:
        return out
    # A choice in no map list gets a class of its own, so it never matches.
    pairs = []
    no_judge = 0
    confusion = {
        value: {c.key: 0 for c in card.choices if c.key != CANT_TELL} for value in a.map
    }
    for judge, label in rows:
        answer = label["answer"]
        if answer == CANT_TELL:
            continue
        judge_class = a.judge_class(judge)
        if judge_class is None:
            no_judge += 1
            continue
        pairs.append((a.human_class(answer) or ("unmapped", answer), judge_class))
        confusion[judge_class][answer] += 1
    agreed = sum(1 for h, j in pairs if h == j)
    low, high = wilson(agreed, len(pairs))
    k = kappa(pairs)
    boot = bootstrap_kappa(pairs) if k is not None else None
    out["agreement"] = {
        "compared": len(pairs),
        "agreed": agreed,
        "rate": agreed / len(pairs) if pairs else None,
        "range": [low, high],
        "no_judge": no_judge,
        "judge_values": sorted({j for _, j in pairs}),
        "kappa": k,
        "kappa_range": [boot[0], boot[1]] if boot else None,
        "kappa_resamples": boot[2] if boot else 0,
        "confusion": confusion,
    }
    compares = []
    for followup_key, judge_field in a.compare:
        f = card.followup(followup_key)
        both = [
            (label["followups"][followup_key], judge[judge_field])
            for judge, label in rows
            if followup_key in label.get("followups", {})
            and isinstance(judge, dict)
            and judge.get(judge_field) is not None
        ]
        same = sum(
            1
            for mine, theirs in both
            if _same(f.kind, mine, theirs if f.kind == "text" else judge_key(theirs))
        )
        low, high = wilson(same, len(both))
        compares.append(
            {
                "followup": followup_key,
                "judge_field": judge_field,
                "compared": len(both),
                "agreed": same,
                "range": [low, high],
            }
        )
    out["agreement"]["compare"] = compares
    return out


def gate_result(card: Card, overall: dict) -> dict | None:
    """The card's gate against the overall numbers: the lower ends must clear."""
    gate = card.gate
    if gate is None:
        return None
    a = overall["agreement"]
    checks = []
    if gate.agreement_lower is not None:
        low = a["range"][0]
        checks.append(
            {
                "check": "agreement_lower",
                "need": gate.agreement_lower,
                "got": low,
                "passed": a["compared"] > 0 and low >= gate.agreement_lower,
            }
        )
    if gate.kappa_lower is not None:
        low = a["kappa_range"][0] if a["kappa_range"] else None
        checks.append(
            {
                "check": "kappa_lower",
                "need": gate.kappa_lower,
                "got": low,
                "passed": low is not None and low >= gate.kappa_lower,
            }
        )
    enough = a["compared"] >= gate.min_n
    return {
        "min_n": gate.min_n,
        "compared": a["compared"],
        "enough": enough,
        "checks": checks,
        "passed": enough and all(c["passed"] for c in checks),
    }


def build(task: Task, by: str | None = None) -> dict:
    rows = [(task.item(i).judge, label) for i, label in task.labels.current.items()]
    overall = bucket(task.card, rows)
    report: dict[str, Any] = {
        "format": REPORT_FORMAT,
        "title": task.card.title,
        "card_version": task.card.version,
        "pin": task.card.pin,
        "generated_at": store.now(),
        "items": len(task.items),
        "labeled": len(rows),
        "older": len(task.labels.older),
        "overall": overall,
        "gate": gate_result(task.card, overall) if task.card.agreement else None,
        "by": by,
        "buckets": [],
    }
    if by:
        groups: dict[str, list] = {}
        for item_id, label in task.labels.current.items():
            item = task.item(item_id)
            value = item.meta.get(by)
            key = NO_VALUE if value is None else judge_key(value)
            groups.setdefault(key, []).append((item.judge, label))
        report["buckets"] = [
            {"value": key, **bucket(task.card, groups[key])} for key in sorted(groups)
        ]
    return report


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{x * 100:.0f}%"


def _num(x: float | None) -> str:
    return "-" if x is None else f"{x:.2f}"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _agreement_lines(card: Card, b: dict) -> list[str]:
    a = b["agreement"]
    if not a["compared"]:
        return ["No labels to compare with the judge yet."]
    lines = [
        f"Agreement with the judge: {a['agreed']} of {a['compared']} "
        f"({_pct(a['rate'])}), 95% range {_pct(a['range'][0])} to "
        f"{_pct(a['range'][1])}."
    ]
    if a["kappa"] is None:
        lines.append(
            "Kappa: not defined, because the judge gave only one answer "
            f"({', '.join(a['judge_values'])}). Use the agreement range instead."
        )
    elif a["kappa_range"] is None:
        lines.append(f"Kappa: {_num(a['kappa'])} (too few labels for a range).")
    else:
        low, high = a["kappa_range"]
        lines.append(
            f"Kappa: {_num(a['kappa'])}, 95% range {_num(low)} to {_num(high)} "
            f"({a['kappa_resamples']} resamples)."
        )
    if a["no_judge"]:
        lines.append(f"Labels on items with no judge answer: {a['no_judge']}.")
    keys = [c.key for c in card.choices if c.key != CANT_TELL]
    lines += [
        "",
        "Rows are the judge's answer, columns are yours.",
        "",
        "| judge | " + " | ".join(keys) + " |",
        "| --- |" + " ---: |" * len(keys),
    ]
    for value, row in a["confusion"].items():
        cells = " | ".join(str(row[k]) for k in keys)
        lines.append(f"| {_cell(value)} | {cells} |")
    for c in a["compare"]:
        lines.append("")
        lines.append(
            f"Follow-up `{c['followup']}` against the judge's `{c['judge_field']}`: "
            f"{c['agreed']} of {c['compared']} agree, 95% range "
            f"{_pct(c['range'][0])} to {_pct(c['range'][1])}."
        )
    return lines


def markdown(task: Task, report: dict) -> str:
    card = task.card
    o = report["overall"]
    lines = [
        f"# {report['title']}",
        "",
        f"Made {report['generated_at']} from {report['labeled']} labels on "
        f"{report['items']} items, card version {report['card_version']}.",
    ]
    if report["older"]:
        lines.append(
            f"{report['older']} items have labels only under an older card; "
            "they are not counted."
        )
    lines += ["", "## Answers", "", "| choice | count |", "| --- | ---: |"]
    for c in card.choices:
        lines.append(f"| {_cell(c.label)} | {o['choices'][c.key]} |")
    if o["n"]:
        lines += ["", f"Can't tell: {_pct(o['cant_tell_rate'])} of labels."]
    if "agreement" in o:
        lines += ["", "## Against the judge", "", *_agreement_lines(card, o)]
    gate = report["gate"]
    if gate:
        lines += ["", "## Gate", ""]
        need = [f"at least {gate['min_n']} labels compared"]
        for c in gate["checks"]:
            what = "agreement" if c["check"] == "agreement_lower" else "kappa"
            got = _pct(c["got"]) if what == "agreement" else _num(c["got"])
            want = _pct(c["need"]) if what == "agreement" else _num(c["need"])
            need.append(f"{what} range low end at least {want} (now {got})")
        lines.append("Needs " + "; ".join(need) + ".")
        if gate["passed"]:
            verdict = "passed"
        elif not gate["enough"]:
            verdict = f"not yet: {gate['compared']} of {gate['min_n']} compared"
        else:
            verdict = "failed"
        lines += ["", f"Result: **{verdict}**."]
    if report["by"]:
        lines += [
            "",
            f"## By {report['by']}",
            "",
            "| value | labels | can't tell | agreed | 95% range | kappa |",
            "| --- | ---: | ---: | ---: | --- | ---: |",
        ]
        for b in report["buckets"]:
            a = b.get("agreement")
            agreed = f"{a['agreed']} of {a['compared']}" if a else "-"
            span = f"{_pct(a['range'][0])} to {_pct(a['range'][1])}" if a else "-"
            k = _num(a["kappa"]) if a else "-"
            lines.append(
                f"| {_cell(b['value'])} | {b['n']} | {_pct(b['cant_tell_rate'])} "
                f"| {agreed} | {span} | {k} |"
            )
    return "\n".join(lines) + "\n"


def write(task: Task, by: str | None = None) -> dict:
    """Build the report and write report.md and report.json into the task folder."""
    report = build(task, by)
    store.atomic_write(task.dir / "report.md", markdown(task, report))
    store.atomic_write(
        task.dir / "report.json",
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
    )
    return report
