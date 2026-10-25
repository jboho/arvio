# arvio task format, version 1

A task is one folder with three files. Your project writes `task.toml` and `items.jsonl`; arvio writes `labels.jsonl`. Your project never imports arvio: it writes the first two files and reads the third.

```
my-task/
  task.toml      the task card: question, choices, follow-ups, how to compare with the judge
  items.jsonl    one item per line: what the labeler sees, plus the hidden judge answer
  labels.jsonl   append-only log written by arvio
```

`arvio check DIR` validates all three and exits 1 on any problem. A problem in `items.jsonl` or `labels.jsonl` names the file and line; a problem in `task.toml` names the file and the setting (for example `choice 3 hotkey`). Unknown keys are errors, so a typo never silently drops a setting.

## task.toml

```toml
format = 1
title = "Is the draft answer right?"
purpose = "These labels decide which gold answers we trust."
question = "Does the draft answer match the source text?"
rules = ["Judge only against the source text shown."]
session_size = 10        # items per session, 1-100; default 10
seconds_per_item = 45    # first estimate, 1-3600, before arvio has measured your pace; default 60
pin = "judge-prompt-3f1a9c0e"   # optional: changes when the judge's instructions change

[[choices]]
key = "correct"          # lowercase letters, digits, underscore
hotkey = "c"             # one lowercase letter or digit
label = "Correct"
meaning = "The answer says what the source says."
example = "Source: 'The library opens at 09:00.' Answer: '09:00'."   # optional

[[choices]]
key = "wrong"
hotkey = "w"
label = "Wrong"
meaning = "The answer contradicts the source or adds something it does not say."

[[choices]]
key = "cant_tell"        # required in every task
hotkey = "u"
label = "Can't tell"
meaning = "The text shown is not enough to decide."

[[followups]]
key = "why"
when = ["wrong"]         # shown only after one of these choices is picked
kind = "single"          # single | multi | text | edit
required = true          # default false
prompt = "What is wrong with it?"
options = [
  { key = "contradicts", hotkey = "1", label = "Contradicts", meaning = "The source says otherwise." },
  { key = "unsupported", hotkey = "2", label = "Unsupported", meaning = "The source does not say it." },
]

[[followups]]
key = "fix"
when = ["wrong"]
kind = "edit"
prompt = "Optional: write the right answer."
fields = [{ key = "answer", label = "Right answer", multiline = false }]

[agreement]              # optional: how to compare labels with the judge
judge_field = "verdict"  # the key in each item's "judge" object
map = { pass = ["correct"], fail = ["wrong"] }   # judge value -> choices that agree with it
compare = [{ followup = "why", judge_field = "reason" }]   # optional

[gate]                   # optional: a pass rule for the report; needs [agreement]
min_n = 100              # at least this many items compared with the judge
kappa_lower = 0.60       # the low end of kappa's 95% range is at least this
agreement_lower = 0.70   # the low end of raw agreement's 95% range is at least this
```

Rules:

- Keys (choices, follow-ups, options, fields) use 1-40 of `a-z 0-9 _`.
- Hotkeys are unique across the whole card (choices and every follow-up option). `n` is reserved for the note field.
- `choices` needs `cant_tell` and at least one other choice.
- `when` is required and lists the choices that show the follow-up.
- `single` and `multi` follow-ups need `options`. `edit` needs `fields`, each `{ key, label, multiline }`. Each field starts with the item's `prefill[key]`. `text` takes free text.
- A required `edit` needs at least one field changed from its prefill; the page won't save it unchanged.
- A judge value that is not a string (for example `true`) is written in `map` as its JSON text: `map = { true = ["correct"], false = ["partly", "wrong"] }`.
- A choice may appear in at most one `map` list. `cant_tell` may not appear. A choice in no list counts as disagreeing with every judge value.
- `compare` names a `single` or `text` follow-up and the judge field to compare it with.
- A gate sets `kappa_lower` (between -1 and 1), `agreement_lower` (between 0 and 1) or both, and passes only when every one it sets holds at `min_n` (1 or more) or more.
- If you generate `task.toml` from code, strings written with `json.dumps` (Python) or `JSON.stringify` (JS) are valid TOML strings.

### Task version

arvio hashes the question, each choice's key and meaning, each follow-up (key, `when`, kind, `required`, prompt, option keys and meanings, field keys) and `pin` into a 12-character task version. Every label records the version it was made under. Change any of those, and older labels stop counting: `arvio status` shows how many there are, and the items come back as unlabeled. Title, purpose, rules, labels, hotkeys, examples, `session_size`, `seconds_per_item` and the order of choices, follow-ups and options are not part of the hash, so changing them keeps your labels.

An item you labeled and compared under the old version comes back under the new one. You saw the judge's old answer for it, so keep that in mind, or change the items too.

## items.jsonl

One JSON object per line:

```json
{"id": "q01-5d1c2e", "display": [{"kind": "text", "title": "Question", "text": "When does the library open?"}, {"kind": "text", "title": "Draft answer", "text": "09:00"}], "prefill": {"answer": "09:00"}, "judge": {"verdict": "pass"}, "meta": {"topic": "opening-hours"}}
```

| Key | Required | Meaning |
|---|---|---|
| `id` | yes | Unique, up to 200 characters of `A-Z a-z 0-9 . _ : -`, starting with a letter or digit. Use a hash of the item's content, so exporting again keeps the same ids. The page doesn't show the id while you label, but it does show it on the results screen and it is in the data the page loads, so keep `meta` and `judge` values (a model name, a run number) out of it. `arvio check` warns when an id contains one. |
| `display` | yes | The blocks shown to the labeler, in order. At least one. |
| `prefill` | no | Strings that start the `edit` fields, keyed by field key. A key that is no edit field on the card is an error. |
| `judge` | no | The judge's answer. arvio never sends it to the page until the session ends. `null` or missing means no judge for this item. When the card has `[agreement]`, a `judge` object must hold `judge_field` with a value from `map`, and a field named in `compare` must hold an option key of its `single` follow-up, or a string for a `text` follow-up. |
| `meta` | no | Anything you want for report buckets (`arvio report --by KEY`). Never sent to the page. |

Display blocks:

| Kind | Keys |
|---|---|
| `text` | `text`; optional `title`, `mono` (bool), `highlight` (list of exact substrings of `text`), `collapsed` (bool) |
| `diff` | `text` (a unified diff); optional `title`, `collapsed` |
| `fields` | `fields` (list of `[label, value]` string pairs); optional `title`, `collapsed` |

All text is shown as plain text. Nothing is rendered as HTML or Markdown.

In both JSONL files, a key that appears twice in one object, `NaN` or `Infinity`, and half of a surrogate pair are errors.

Write `items.jsonl` to a temp file and rename it into place, so arvio never reads half a file. Keep every item that already has a label under the current card: a label whose item is gone is an error. (A label under an older card whose item is gone only gets a warning.)

## labels.jsonl

arvio appends one line per event and syncs it to disk before answering the page. Two kinds of line:

```json
{"kind": "label", "item_id": "q01-5d1c2e", "item_hash": "9b04e1c2a7f3", "task_version": "c41d09a2b6e1", "answer": "wrong", "followups": {"why": "unsupported"}, "note": "", "seconds": 38.4, "labeled_at": "2026-10-19T16:02:11Z", "labeler": "sam", "session": "s-20261019T160000Z-ab12"}
{"kind": "reveal", "item_ids": ["q01-5d1c2e"], "task_version": "c41d09a2b6e1", "at": "2026-10-19T16:09:40Z", "session": "s-20261019T160000Z-ab12"}
```

- `followups` holds one value per answered follow-up: an option key (`single`), a list of option keys (`multi`), a string (`text`), or an object of field values (`edit`).
- `item_hash` is a hash of what the labeler saw (`display` and `prefill`). If it no longer matches the item, the item changed after it was labeled; `arvio check` warns.
- `seconds` counts only the time the page was visible, from 0 to 86400.

**How to read it:** go through the lines in order and keep only rows whose `task_version` matches the current card. For each item, the latest `label` row wins. A `reveal` row locks those of the items it names that have a label by then: the labeler saw the judge's answer, so arvio refuses any later label for them under the same version. An item a reveal names before it has a label stays open.

You don't have to write that reader yourself. `arvio labels DIR` applies the rule and prints one JSON line per labeled item, in `items.jsonl` order: the `label` row without its `kind`, plus `"locked": true` once the item is revealed. It exits 1 and prints nothing when the task has an error, so a script never reads a half-checked log.

`arvio check` also warns, without failing, when a label comes after its item's reveal (it is ignored), when a reveal names an item with no label, when an item changed after it was labeled, and when an item's id contains one of its `meta` or `judge` values.

A last line with no newline that doesn't parse is a write that was cut short (or is still in progress). Readers skip it with a warning. Before its next append, arvio moves that text to `labels.jsonl.torn` and starts on a clean line.

## The inbox

`arvio add DIR` checks a task folder and records its absolute path in `$ARVIO_DATA_DIR/tasks.json` (default `~/.arvio`). `arvio remove DIR` takes it off; the task's files stay. The registry holds paths only, never items or labels.

## The labeling page

`arvio` with no command starts a small web server for your inbox and opens the page in your browser (`--no-browser` prints the link instead). Stop it with Ctrl-C. With an empty inbox it prints how to add a task and exits.

- The server listens only on 127.0.0.1, on a free port. The link carries a key that changes every time arvio starts; the page sends it back with every API request, and the server refuses API requests without it. It refuses every request from another host name or another page.
- One arvio runs per inbox (it holds `$ARVIO_DATA_DIR/server.lock`), and it locks every task folder it serves. A folder another arvio holds shows as "open in another arvio" and can't be started.
- Items come in a fixed order made from a hash of the card version and the item id, never the file's or the judge's order. There is no skip: "Can't tell" is the way out.
- Keys: a choice's or option's hotkey picks it, `N` opens the note, Enter saves and moves on, ← goes back one item. On the inbox, Enter or `1`-`9` opens a task.
- Each label is appended to `labels.jsonl` and synced to disk when you save it. `labeler` is `$ARVIO_LABELER`, or your login name if it is not set.
- Finish (or Enter on the last screen) writes one `reveal` row for every item labeled under the current card and not yet revealed, including any left from a session you closed early, and only then shows the judge's answers: how many you agreed on, and each disagreement. Those labels are locked from then on. A card with no `[agreement]` has no judge to show, so Finish only locks the labels and counts them per choice.
- The page measures your pace from the time it was visible, and uses the median of your last 20 labels once you have 3.

## arvio report

`arvio report DIR [--by META_KEY] [--json]` reads the current labels (the latest per item under the current card) and writes `report.md` and `report.json` into the task folder, each by temp file and rename.

- Counts per choice and the share of "can't tell".
- With `[agreement]`: how many labels agree with the judge, with a 95% Wilson range; Cohen's kappa with a 95% range from 1,000 bootstrap resamples (seed 42, so the same labels give the same range); and a table of the judge's answers against yours. Kappa is not defined when the judge gave only one answer across the compared items. "Can't tell" labels and items with no judge answer are counted but never compared.
- Each `compare` entry: how often the follow-up matches the judge's field (text compared without case or surrounding spaces).
- `--by KEY` repeats the numbers per value of `meta[KEY]`; items without it go under `(none)`.
- With `[gate]`: passes only when at least `min_n` labels were compared and the low end of every range the gate names clears its threshold.

Exit codes: 0 when the report was written and the gate passed or there is none, 2 when the gate did not pass (including too few labels), 1 when the task has errors or the report can't be written. `--json` prints `report.json` instead of the Markdown. Its `format` key is 1.
