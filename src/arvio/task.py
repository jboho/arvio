"""Read and check a task folder: task.toml, items.jsonl and labels.jsonl.

The format is specified in docs/format.md. Every reader here fails closed: a
problem is reported with its file, and with its line in the JSONL files (a
task.toml problem names the setting instead), and a task with an error never
loads.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
import tomllib
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

FORMAT = 1
CANT_TELL = "cant_tell"
NOTE_HOTKEY = "n"
FOLLOWUP_KINDS = ("single", "multi", "text", "edit")
BLOCK_KINDS = ("text", "diff", "fields")
KEY_RE = re.compile(r"[a-z0-9_]{1,40}")
HOTKEY_RE = re.compile(r"[a-z0-9]")
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}")
ID_PART_RE = re.compile(r"[._:-]")
MAX_SECONDS = 86400
PACE_WINDOW = 20
PACE_MIN_LABELS = 3

CARD_FILE = "task.toml"
ITEMS_FILE = "items.jsonl"
LABELS_FILE = "labels.jsonl"


@dataclass(frozen=True)
class Problem:
    file: Path
    line: int | None
    message: str
    warning: bool = False

    def __str__(self) -> str:
        where = f"{self.file}:{self.line}" if self.line else str(self.file)
        level = "warning" if self.warning else "error"
        return _printable(f"{where}: {level}: {self.message}")


def _printable(text: str) -> str:
    """Escape control characters: messages quote file contents, and an escape
    sequence in a judge value must not reach the terminal as one."""
    return "".join(c if c.isprintable() else repr(c)[1:-1] for c in text)


class TaskError(Exception):
    """A task folder with at least one error. Carries every problem found."""

    def __init__(self, problems: list[Problem]):
        self.problems = problems
        super().__init__("\n".join(map(str, problems)))


@dataclass(frozen=True)
class Option:
    key: str
    hotkey: str
    label: str
    meaning: str
    example: str = ""


@dataclass(frozen=True)
class EditField:
    key: str
    label: str
    multiline: bool = False


@dataclass(frozen=True)
class Followup:
    key: str
    when: tuple[str, ...]
    kind: str
    prompt: str
    required: bool = False
    options: tuple[Option, ...] = ()
    fields: tuple[EditField, ...] = ()


@dataclass(frozen=True)
class Agreement:
    judge_field: str
    map: dict[str, tuple[str, ...]]
    compare: tuple[tuple[str, str], ...] = ()

    def judge_class(self, judge: Any) -> str | None:
        """The map key for an item's judge answer; None when there is none."""
        if not isinstance(judge, dict):
            return None
        value = judge.get(self.judge_field)
        return None if value is None else judge_key(value)

    def human_class(self, answer: str) -> str | None:
        """The judge value a choice agrees with; None for a choice in no list."""
        for value, choices in self.map.items():
            if answer in choices:
                return value
        return None


@dataclass(frozen=True)
class Gate:
    min_n: int
    kappa_lower: float | None = None
    agreement_lower: float | None = None


@dataclass(frozen=True)
class Card:
    title: str
    question: str
    choices: tuple[Option, ...]
    version: str
    purpose: str = ""
    rules: tuple[str, ...] = ()
    session_size: int = 10
    seconds_per_item: float = 60.0
    pin: str = ""
    followups: tuple[Followup, ...] = ()
    agreement: Agreement | None = None
    gate: Gate | None = None

    def choice(self, key: str) -> Option | None:
        return next((c for c in self.choices if c.key == key), None)

    def followup(self, key: str) -> Followup | None:
        return next((f for f in self.followups if f.key == key), None)


@dataclass(frozen=True)
class Item:
    id: str
    display: list
    prefill: dict
    judge: dict | None
    meta: dict
    hash: str
    line: int


@dataclass
class Labels:
    current: dict[str, dict] = field(default_factory=dict)
    revealed: set[str] = field(default_factory=set)
    older: set[str] = field(default_factory=set)
    seconds: list[float] = field(default_factory=list)
    last_at: str = ""


@dataclass
class Task:
    dir: Path
    card: Card
    items: list[Item]
    labels: Labels

    @cached_property
    def _by_id(self) -> dict[str, Item]:
        return {i.id: i for i in self.items}

    def item(self, item_id: str) -> Item | None:
        return self._by_id.get(item_id)

    def waiting(self) -> list[Item]:
        return [i for i in self.items if i.id not in self.labels.current]

    def unfinished(self) -> bool:
        """True while items wait for a label or labels wait for the session's end."""
        return bool(self.waiting()) or len(self.labels.current) > len(
            self.labels.revealed
        )

    def pace(self) -> float:
        """Seconds per item: the median of recent labels once there are enough."""
        recent = self.labels.seconds[-PACE_WINDOW:]
        if len(recent) >= PACE_MIN_LABELS:
            return statistics.median(recent)
        return self.card.seconds_per_item

    def summary(self) -> dict:
        waiting = len(self.waiting())
        next_n = min(waiting, self.card.session_size)
        minutes = math.ceil(next_n * self.pace() / 60) if next_n else 0
        return {
            "title": self.card.title,
            "purpose": self.card.purpose,
            "items": len(self.items),
            "waiting": waiting,
            "labeled": len(self.labels.current),
            "revealed": len(self.labels.revealed),
            "unfinished": self.unfinished(),
            "older": len(self.labels.older),
            "has_judge": self.card.agreement is not None,
            "last_at": self.labels.last_at,
            "next_n": next_n,
            "next_minutes": max(minutes, 1) if next_n else 0,
        }


def judge_key(value: Any) -> str:
    """How a judge value is written as a key in the agreement map."""
    return value if isinstance(value, str) else json.dumps(value)


def short_hash(obj: Any) -> str:
    text = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()[:12]


# Each type check is (name for messages, test). bool is an int in Python, so
# the number checks exclude it explicitly.
TEXT = ("a non-empty string", lambda v: isinstance(v, str) and v.strip() != "")
STR = ("a string", lambda v: isinstance(v, str))
BOOL = ("true or false", lambda v: isinstance(v, bool))
INT = ("a whole number", lambda v: isinstance(v, int) and not isinstance(v, bool))
NUM = (
    "a number",
    lambda v: isinstance(v, int | float) and not isinstance(v, bool),
)
STRS = (
    "a list of strings",
    lambda v: isinstance(v, list) and all(isinstance(x, str) for x in v),
)
TABLES = (
    "a list of tables",
    lambda v: isinstance(v, list) and all(isinstance(x, dict) for x in v),
)
SECONDS = (
    f"a number from 0 to {MAX_SECONDS}",
    lambda v: (
        isinstance(v, int | float) and not isinstance(v, bool) and 0 <= v <= MAX_SECONDS
    ),
)
TABLE = ("a table", lambda v: isinstance(v, dict))
OBJECT = ("an object", lambda v: isinstance(v, dict))

CARD_SCHEMA = {
    "format": (INT, True),
    "title": (TEXT, True),
    "question": (TEXT, True),
    "choices": (TABLES, True),
    "purpose": (STR, False),
    "rules": (STRS, False),
    "session_size": (INT, False),
    "seconds_per_item": (NUM, False),
    "pin": (STR, False),
    "followups": (TABLES, False),
    "agreement": (TABLE, False),
    "gate": (TABLE, False),
}
OPTION_SCHEMA = {
    "key": (TEXT, True),
    "hotkey": (TEXT, True),
    "label": (TEXT, True),
    "meaning": (TEXT, True),
    "example": (STR, False),
}
FOLLOWUP_SCHEMA = {
    "key": (TEXT, True),
    "when": (STRS, True),
    "kind": (TEXT, True),
    "prompt": (TEXT, True),
    "required": (BOOL, False),
    "options": (TABLES, False),
    "fields": (TABLES, False),
}
FIELD_SCHEMA = {"key": (TEXT, True), "label": (TEXT, True), "multiline": (BOOL, False)}
AGREEMENT_SCHEMA = {
    "judge_field": (TEXT, True),
    "map": (TABLE, True),
    "compare": (TABLES, False),
}
COMPARE_SCHEMA = {"followup": (TEXT, True), "judge_field": (TEXT, True)}
GATE_SCHEMA = {
    "min_n": (INT, True),
    "kappa_lower": (NUM, False),
    "agreement_lower": (NUM, False),
}
ITEM_SCHEMA = {
    "id": (TEXT, True),
    "display": (("a list of blocks", lambda v: isinstance(v, list)), True),
    "prefill": (OBJECT, False),
    "judge": (("an object or null", lambda v: v is None or isinstance(v, dict)), False),
    "meta": (OBJECT, False),
}
BLOCK_SCHEMAS = {
    "text": {
        "kind": (STR, True),
        "text": (STR, True),
        "title": (STR, False),
        "mono": (BOOL, False),
        "highlight": (STRS, False),
        "collapsed": (BOOL, False),
    },
    "diff": {
        "kind": (STR, True),
        "text": (STR, True),
        "title": (STR, False),
        "collapsed": (BOOL, False),
    },
    "fields": {
        "kind": (STR, True),
        "fields": (
            ("a list of [label, value] pairs", lambda v: isinstance(v, list)),
            True,
        ),
        "title": (STR, False),
        "collapsed": (BOOL, False),
    },
}
LABEL_SCHEMA = {
    "kind": (STR, True),
    "item_id": (TEXT, True),
    "task_version": (TEXT, True),
    "answer": (TEXT, True),
    "item_hash": (STR, False),
    "followups": (OBJECT, False),
    "note": (STR, False),
    "seconds": (SECONDS, False),
    "labeled_at": (STR, False),
    "labeler": (STR, False),
    "session": (STR, False),
}
REVEAL_SCHEMA = {
    "kind": (STR, True),
    "item_ids": (STRS, True),
    "task_version": (TEXT, True),
    "at": (STR, False),
    "session": (STR, False),
}


def _where(where: str, key: str) -> str:
    return f"{where} {key}" if where else key


def _shape(
    errs: list[str], value: Any, where: str, schema: dict, partial: bool = False
) -> dict | None:
    """Check a table against a schema; None if a required key is missing or bad.

    With partial, return the keys that passed anyway, so later checks still run.
    """
    if not isinstance(value, dict):
        errs.append(f"{where or 'top level'}: must be a table")
        return {} if partial else None
    for key in sorted(value.keys() - schema.keys()):
        errs.append(f"{_where(where, key)}: unknown key")
    out, ok = {}, True
    for key, ((name, test), required) in schema.items():
        if key not in value:
            if required:
                errs.append(f"{_where(where, key)}: missing")
                ok = False
        elif test(value[key]):
            out[key] = value[key]
        else:
            errs.append(f"{_where(where, key)}: must be {name}")
            ok = ok and not required
    return out if ok or partial else None


def parse_card(data: dict) -> tuple[Card | None, list[str]]:
    """Build a Card from parsed TOML. Returns (None, errors) on any error."""
    errs: list[str] = []
    top = _shape(errs, data, "", CARD_SCHEMA, partial=True)
    if "format" in top and top["format"] != FORMAT:
        errs.append(
            f"format: arvio reads format {FORMAT}, this card is {top['format']}"
        )
    hotkeys: dict[str, str] = {}

    def option(raw: Any, where: str) -> Option | None:
        o = _shape(errs, raw, where, OPTION_SCHEMA)
        if o is None:
            return None
        if not KEY_RE.fullmatch(o["key"]):
            errs.append(f"{where} key: use 1-40 of a-z 0-9 _")
        hotkey = o["hotkey"]
        if not HOTKEY_RE.fullmatch(hotkey):
            errs.append(f"{where} hotkey: must be one lowercase letter or digit")
        elif hotkey == NOTE_HOTKEY:
            errs.append(f"{where} hotkey: '{NOTE_HOTKEY}' is kept for the note")
        elif hotkey in hotkeys:
            errs.append(
                f"{where} hotkey: '{hotkey}' is already used by {hotkeys[hotkey]}"
            )
        else:
            hotkeys[hotkey] = where
        return Option(o["key"], hotkey, o["label"], o["meaning"], o.get("example", ""))

    def unique(keys: list[str], where: str) -> None:
        for key in sorted({k for k in keys if keys.count(k) > 1}):
            errs.append(f"{where}: key '{key}' is used more than once")

    choices = []
    for i, raw in enumerate(top.get("choices", []), 1):
        if c := option(raw, f"choice {i}"):
            choices.append(c)
    choice_keys = [c.key for c in choices]
    unique(choice_keys, "choices")
    if "choices" in top and CANT_TELL not in choice_keys:
        errs.append(f"choices: a '{CANT_TELL}' choice is required")
    if "choices" in top and len(set(choice_keys) - {CANT_TELL}) < 1:
        errs.append(f"choices: needs at least one choice besides '{CANT_TELL}'")

    followups = []
    for i, raw in enumerate(top.get("followups", []), 1):
        where = f"follow-up {i}"
        f = _shape(errs, raw, where, FOLLOWUP_SCHEMA)
        if f is None:
            continue
        kind = f["kind"]
        if not KEY_RE.fullmatch(f["key"]):
            errs.append(f"{where} key: use 1-40 of a-z 0-9 _")
        if kind not in FOLLOWUP_KINDS:
            errs.append(f"{where} kind: must be one of {', '.join(FOLLOWUP_KINDS)}")
        if not f["when"]:
            errs.append(f"{where} when: list at least one choice")
        for key in f["when"]:
            if key not in choice_keys:
                errs.append(f"{where} when: '{key}' is not a choice")
        options = []
        if kind in ("single", "multi"):
            if not f.get("options"):
                errs.append(f"{where} options: a {kind} follow-up needs options")
            for j, raw_option in enumerate(f.get("options", []), 1):
                if o := option(raw_option, f"{where} option {j}"):
                    options.append(o)
            unique([o.key for o in options], f"{where} options")
        elif "options" in f:
            errs.append(
                f"{where} options: only single and multi follow-ups take options"
            )
        edit_fields = []
        if kind == "edit":
            if not f.get("fields"):
                errs.append(f"{where} fields: an edit follow-up needs fields")
            for j, raw_field in enumerate(f.get("fields", []), 1):
                x = _shape(errs, raw_field, f"{where} field {j}", FIELD_SCHEMA)
                if x is None:
                    continue
                if not KEY_RE.fullmatch(x["key"]):
                    errs.append(f"{where} field {j} key: use 1-40 of a-z 0-9 _")
                edit_fields.append(
                    EditField(x["key"], x["label"], x.get("multiline", False))
                )
            unique([x.key for x in edit_fields], f"{where} fields")
        elif "fields" in f:
            errs.append(f"{where} fields: only edit follow-ups take fields")
        followups.append(
            Followup(
                f["key"],
                tuple(f["when"]),
                kind,
                f["prompt"],
                f.get("required", False),
                tuple(options),
                tuple(edit_fields),
            )
        )
    unique([f.key for f in followups], "followups")

    agreement = None
    if "agreement" in top:
        a = _shape(errs, top["agreement"], "agreement", AGREEMENT_SCHEMA)
        if a is not None:
            mapping: dict[str, tuple[str, ...]] = {}
            listed: dict[str, str] = {}
            for value, keys in a["map"].items():
                if not (
                    isinstance(keys, list)
                    and keys
                    and all(isinstance(k, str) for k in keys)
                ):
                    errs.append(
                        f"agreement map {value}: must be a non-empty list of choices"
                    )
                    continue
                for key in keys:
                    if key not in choice_keys:
                        errs.append(f"agreement map {value}: '{key}' is not a choice")
                    elif key == CANT_TELL:
                        errs.append(
                            f"agreement map {value}: '{CANT_TELL}' can't be mapped"
                        )
                    elif key in listed:
                        errs.append(
                            f"agreement map: '{key}' is listed under both "
                            f"'{listed[key]}' and '{value}'"
                        )
                    else:
                        listed[key] = value
                mapping[value] = tuple(keys)
            if not a["map"]:
                errs.append("agreement map: list at least one judge value")
            compare = []
            for i, raw in enumerate(a.get("compare", []), 1):
                where = f"agreement compare {i}"
                c = _shape(errs, raw, where, COMPARE_SCHEMA)
                if c is None:
                    continue
                target = next((f for f in followups if f.key == c["followup"]), None)
                if target is None:
                    errs.append(
                        f"{where} followup: '{c['followup']}' is not a follow-up"
                    )
                elif target.kind not in ("single", "text"):
                    errs.append(
                        f"{where} followup: only single and text follow-ups compare"
                    )
                compare.append((c["followup"], c["judge_field"]))
            agreement = Agreement(a["judge_field"], mapping, tuple(compare))

    gate = None
    if "gate" in top:
        g = _shape(errs, top["gate"], "gate", GATE_SCHEMA)
        if "agreement" not in top:
            errs.append("gate: needs an [agreement] section to compare against")
        if g is not None:
            if g["min_n"] < 1:
                errs.append("gate min_n: must be at least 1")
            if "kappa_lower" not in g and "agreement_lower" not in g:
                errs.append("gate: set kappa_lower, agreement_lower or both")
            if not -1 <= g.get("kappa_lower", 0) <= 1:
                errs.append("gate kappa_lower: must be between -1 and 1")
            if not 0 <= g.get("agreement_lower", 0) <= 1:
                errs.append("gate agreement_lower: must be between 0 and 1")
            gate = Gate(g["min_n"], g.get("kappa_lower"), g.get("agreement_lower"))

    session_size = top.get("session_size", 10)
    if not 1 <= session_size <= 100:
        errs.append("session_size: must be between 1 and 100")
    seconds = top.get("seconds_per_item", 60)
    if not 1 <= seconds <= 3600:
        errs.append("seconds_per_item: must be between 1 and 3600")
    rules = top.get("rules", [])
    if any(not r.strip() for r in rules):
        errs.append("rules: a rule is empty")

    if errs:
        return None, errs
    pin = top.get("pin", "")
    return (
        Card(
            title=top["title"],
            question=top["question"],
            choices=tuple(choices),
            version=card_version(top["question"], choices, followups, pin),
            purpose=top.get("purpose", ""),
            rules=tuple(rules),
            session_size=session_size,
            seconds_per_item=float(seconds),
            pin=pin,
            followups=tuple(followups),
            agreement=agreement,
            gate=gate,
        ),
        [],
    )


def card_version(question: str, choices, followups, pin: str) -> str:
    """Hash of what the labeler is asked.

    Rewording titles or examples keeps it, and so does reordering: every list
    is sorted by key first.
    """

    def options(opts) -> list:
        return sorted([o.key, o.meaning] for o in opts)

    return short_hash(
        {
            "question": question,
            "choices": options(choices),
            "followups": sorted(
                (
                    {
                        "key": f.key,
                        "when": sorted(f.when),
                        "kind": f.kind,
                        "required": f.required,
                        "prompt": f.prompt,
                        "options": options(f.options),
                        "fields": sorted(x.key for x in f.fields),
                    }
                    for f in followups
                ),
                key=lambda f: f["key"],
            ),
            "pin": pin,
        }
    )


def check_answer(
    card: Card, answer: Any, followups: Any, prefill: dict | None = None
) -> list[str]:
    """Problems with one label's answer and follow-ups under this card.

    Given the item's prefill, a required edit must also change a field.
    """
    if card.choice(answer) is None:
        return [f"answer '{answer}' is not a choice"]
    if not isinstance(followups, dict):
        return ["followups: must be an object"]
    errs = []
    for key, value in followups.items():
        f = card.followup(key)
        if f is None:
            errs.append(f"followups: '{key}' is not a follow-up")
        elif answer not in f.when:
            errs.append(f"followups: '{key}' does not apply to '{answer}'")
        elif (err := _followup_value_error(f, value)) is not None:
            errs.append(f"followups {key}: {err}")
    for f in card.followups:
        if not f.required or answer not in f.when:
            continue
        value = followups.get(f.key)
        if _blank(value):
            errs.append(f"followups: '{f.key}' is required for '{answer}'")
        elif f.kind == "edit" and prefill is not None and _unchanged(f, value, prefill):
            errs.append(f"followups: '{f.key}' must change at least one field")
    return errs


def _unchanged(f: Followup, value: dict, prefill: dict) -> bool:
    return all((value.get(x.key) or "") == (prefill.get(x.key) or "") for x in f.fields)


def _blank(value: Any) -> bool:
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, dict):
        return all(_blank(v) for v in value.values())
    return not value


def _followup_value_error(f: Followup, value: Any) -> str | None:
    option_keys = {o.key for o in f.options}
    if f.kind == "single":
        ok = isinstance(value, str) and value in option_keys
        return None if ok else "must be one of its option keys"
    if f.kind == "multi":
        ok = isinstance(value, list)
        ok = ok and all(isinstance(v, str) and v in option_keys for v in value)
        ok = ok and len(set(value)) == len(value)
        return None if ok else "must be a list of its option keys, each once"
    if f.kind == "text":
        return None if isinstance(value, str) else "must be a string"
    field_keys = {x.key for x in f.fields}
    ok = isinstance(value, dict) and value.keys() <= field_keys
    ok = ok and all(isinstance(v, str) for v in value.values())
    return None if ok else "must be an object of its field keys to strings"


def read_card(file: Path) -> tuple[Card | None, list[Problem]]:
    try:
        data = tomllib.loads(file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, [Problem(file, None, "missing")]
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        return None, [Problem(file, None, f"cannot read: {e}")]
    card, errs = parse_card(data)
    return card, [Problem(file, None, e) for e in errs]


def _object(pairs: list[tuple[str, Any]]) -> dict:
    obj: dict = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"key '{key[:40]}' appears twice")
        obj[key] = value
    return obj


def _no_constant(name: str) -> Any:
    raise ValueError(f"{name} is not valid JSON")


def strict_loads(text: str | bytes) -> Any:
    """json.loads that refuses duplicate keys, NaN, Infinity and lone surrogates.

    Raises ValueError (which covers JSONDecodeError and the Unicode errors) or
    RecursionError.
    """
    value = json.loads(text, object_pairs_hook=_object, parse_constant=_no_constant)
    # A lone surrogate parses but can't be written back as UTF-8.
    json.dumps(value, ensure_ascii=False).encode()
    return value


def read_jsonl(
    file: Path, problems: list[Problem], torn_tail_ok: bool = False
) -> list[tuple[int, Any]] | None:
    """(line number, value) per non-blank line; None if the file can't be read.

    Splits on newlines only: str.splitlines() would also split on U+2028,
    which JSON strings may contain raw. With torn_tail_ok, a last line that
    has no newline and doesn't parse is a write still in progress or cut
    short, so it is skipped with a warning.
    """
    try:
        text = file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        problems.append(Problem(file, None, f"cannot read: {e}"))
        return None
    lines = text.split("\n")
    rows = []
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = strict_loads(line)
        except json.JSONDecodeError as e:
            message = f"not valid JSON: {e.msg} (column {e.colno})"
        except UnicodeEncodeError:
            message = "has a broken character (half of a surrogate pair)"
        except (ValueError, RecursionError) as e:
            message = f"not valid JSON: {str(e)[:120]}"
        else:
            rows.append((n, value))
            continue
        if torn_tail_ok and n == len(lines):
            problems.append(
                Problem(file, n, "the last line is cut short; ignored", warning=True)
            )
        else:
            problems.append(Problem(file, n, message))
    return rows


def _block_errors(block: Any, i: int) -> list[str]:
    where = f"display {i}"
    if not isinstance(block, dict):
        return [f"{where}: must be an object"]
    kind = block.get("kind")
    if kind not in BLOCK_KINDS:
        return [f"{where} kind: must be one of {', '.join(BLOCK_KINDS)}"]
    errs: list[str] = []
    b = _shape(errs, block, where, BLOCK_SCHEMAS[kind])
    if b is None:
        return errs
    for h in b.get("highlight", []):
        if not h or h not in b["text"]:
            errs.append(f"{where} highlight: '{h[:40]}' is not in the block's text")
    if kind == "fields":
        pairs = b["fields"]
        if not all(
            isinstance(p, list) and len(p) == 2 and all(isinstance(x, str) for x in p)
            for p in pairs
        ):
            errs.append(
                f"{where} fields: each must be a [label, value] pair of strings"
            )
    return errs


def _judge_errors(card: Card, judge: dict) -> list[str]:
    """A judge object under a card with [agreement]: no silent "no judge"."""
    a = card.agreement
    value = judge.get(a.judge_field)
    if value is None:
        return [
            f"judge: has no '{a.judge_field}'; write \"judge\": null for an item "
            "with no judge answer"
        ]
    errs = []
    if judge_key(value) not in a.map:
        errs.append(
            f"judge {a.judge_field}: {judge_key(value)[:40]} is not in the card's "
            "agreement map"
        )
    for followup_key, judge_field in a.compare:
        theirs = judge.get(judge_field)
        f = card.followup(followup_key)
        if theirs is None or f is None:
            continue
        if f.kind == "text" and not isinstance(theirs, str):
            errs.append(f"judge {judge_field}: must be a string to compare")
        elif f.kind == "single" and judge_key(theirs) not in {o.key for o in f.options}:
            errs.append(
                f"judge {judge_field}: {judge_key(theirs)[:40]} is not an option "
                f"of follow-up '{followup_key}'"
            )
    return errs


def _id_leaks(item_id: str, judge: Any, meta: dict) -> list[str]:
    """The meta and judge keys whose value shows up in the item's id.

    The page loads the id and the results screen shows it, so such an id can
    tell the labeler what meta and judge are meant to hide. A number or a
    string shorter than 4 counts only as a whole part of the id: a hex hash id
    holds short runs like "42" or "ace" by chance.
    """
    low = item_id.lower()
    parts = set(ID_PART_RE.split(low))
    found: list[str] = []

    def walk(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                walk(v, f"{path}.{k[:40]}")
        elif isinstance(value, list):
            for i, v in enumerate(value):
                walk(v, f"{path}[{i}]")
        elif isinstance(value, str):
            text = value.lower()
            if text in parts or (len(text) >= 4 and text in low):
                found.append(path)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            if str(value) in parts:
                found.append(path)

    walk(meta, "meta")
    walk(judge, "judge")
    return found


def read_items(file: Path, card: Card) -> tuple[list[Item] | None, list[Problem]]:
    problems: list[Problem] = []
    if not file.exists():
        return None, [Problem(file, None, "missing")]
    rows = read_jsonl(file, problems)
    if rows is None:
        return None, problems
    items: list[Item] = []
    first_line: dict[str, int] = {}
    edit_keys = {x.key for f in card.followups for x in f.fields}
    for n, row in rows:
        errs: list[str] = []
        it = _shape(errs, row, "", ITEM_SCHEMA)
        if it is not None:
            item_id = it["id"]
            if not ID_RE.fullmatch(item_id):
                errs.append("id: use up to 200 of A-Z a-z 0-9 . _ : -")
            elif item_id in first_line:
                errs.append(f"id: '{item_id}' is also on line {first_line[item_id]}")
            else:
                first_line[item_id] = n
            if not it["display"]:
                errs.append("display: list at least one block")
            for i, block in enumerate(it["display"], 1):
                errs.extend(_block_errors(block, i))
            prefill = it.get("prefill", {})
            if not all(isinstance(v, str) for v in prefill.values()):
                errs.append("prefill: values must be strings")
            for key in sorted(prefill.keys() - edit_keys):
                errs.append(f"prefill: '{key[:40]}' is not an edit field on the card")
            judge = it.get("judge")
            if card.agreement is not None and judge is not None:
                errs.extend(_judge_errors(card, judge))
            if not errs:
                items.append(
                    Item(
                        id=item_id,
                        display=it["display"],
                        prefill=prefill,
                        judge=judge,
                        meta=it.get("meta", {}),
                        hash=short_hash({"display": it["display"], "prefill": prefill}),
                        line=n,
                    )
                )
                leaks = _id_leaks(item_id, judge, it.get("meta", {}))
                if leaks:
                    problems.append(
                        Problem(
                            file,
                            n,
                            f"id contains the value of {', '.join(leaks[:3])}; "
                            "the labeler can see the id, so use a hash instead",
                            warning=True,
                        )
                    )
        problems.extend(Problem(file, n, e) for e in errs)
    if not rows and not problems:
        problems.append(Problem(file, None, "has no items", warning=True))
    return (
        None if problems and any(not p.warning for p in problems) else items
    ), problems


def read_labels(
    file: Path, card: Card, items: dict[str, Item]
) -> tuple[Labels | None, list[Problem]]:
    """Resolve the label log: the latest label per item wins until a reveal locks it."""
    labels = Labels()
    if not file.exists():
        return labels, []
    problems: list[Problem] = []
    rows = read_jsonl(file, problems, torn_tail_ok=True)
    if rows is None:
        return None, problems
    older: set[str] = set()

    def note(n: int, message: str, warning: bool = False) -> None:
        problems.append(Problem(file, n, message, warning))

    for n, row in rows:
        kind = row.get("kind") if isinstance(row, dict) else None
        errs: list[str] = []
        if kind == "reveal":
            r = _shape(errs, row, "", REVEAL_SCHEMA)
            if r is not None and r["task_version"] == card.version:
                # A reveal locks only labels made before it: arvio shows the
                # judge for labeled items only, so an unlabeled id here was
                # never shown and stays open.
                for item_id in r["item_ids"]:
                    if item_id in labels.current:
                        labels.revealed.add(item_id)
                    else:
                        note(n, f"reveal for '{item_id}', which has no label", True)
        elif kind == "label":
            r = _shape(errs, row, "", LABEL_SCHEMA)
            if r is not None:
                item_id = r["item_id"]
                if "seconds" in r:
                    labels.seconds.append(float(r["seconds"]))
                labels.last_at = max(labels.last_at, r.get("labeled_at", ""))
                if r["task_version"] != card.version:
                    older.add(item_id)
                    if item_id not in items:
                        note(
                            n,
                            f"label for '{item_id}' under an older card; item is gone",
                            True,
                        )
                elif item_id not in items:
                    errs.append(f"item_id: '{item_id}' is not in items.jsonl")
                elif item_id in labels.revealed:
                    note(n, f"label for '{item_id}' after its reveal; ignored", True)
                else:
                    errs.extend(check_answer(card, r["answer"], r.get("followups", {})))
                    if not errs:
                        labels.current[item_id] = r
                        if (
                            r.get("item_hash", items[item_id].hash)
                            != items[item_id].hash
                        ):
                            note(
                                n,
                                f"item '{item_id}' changed after it was labeled",
                                True,
                            )
        else:
            errs.append("kind: must be label or reveal")
        for e in errs:
            note(n, e)
    labels.older = older - labels.current.keys()
    return (None if any(not p.warning for p in problems) else labels), problems


def check_task(folder: Path) -> tuple[Task | None, list[Problem]]:
    """Read every file in a task folder. Task is None if there is any error."""
    folder = Path(folder).expanduser().resolve()
    if not folder.is_dir():
        return None, [Problem(folder, None, "not a folder")]
    card, problems = read_card(folder / CARD_FILE)
    if card is None:
        return None, problems
    items, found = read_items(folder / ITEMS_FILE, card)
    problems += found
    if items is None:
        return None, problems
    labels, found = read_labels(folder / LABELS_FILE, card, {i.id: i for i in items})
    problems += found
    if labels is None:
        return None, problems
    return Task(folder, card, items, labels), problems


def load_task(folder: Path) -> Task:
    task, problems = check_task(folder)
    if task is None:
        raise TaskError([p for p in problems if not p.warning])
    return task
