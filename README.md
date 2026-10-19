<!-- markdownlint-disable MD033 MD041 -->

<h1 align="center">arvio</h1>

<p align="center">
  <strong>Label eval items by hand, one at a time, with the judge's answer hidden until the session ends</strong>
</p>

<p align="center">
  <a href="https://github.com/jboho/arvio/actions/workflows/test.yml"><img src="https://github.com/jboho/arvio/actions/workflows/test.yml/badge.svg" alt="Test status" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-yellow.svg" alt="License: MIT" /></a>
  <img src="https://img.shields.io/badge/status-experimental-orange.svg" alt="Status: experimental" />
  <img src="https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white" alt="Python 3.11+" />
  <img src="https://img.shields.io/badge/runtime%20dependencies-none-brightgreen.svg" alt="No runtime dependencies" />
</p>

<p align="center">
  <sub><em>Arvio</em> is Finnish for "an assessment" · Python standard library only · runs on your own machine</sub>
</p>

---

## Overview

**arvio** is a small local tool for labeling eval items by hand. It shows one item at a time, with the question and the meaning of each answer on screen, and keeps the judge's answer hidden until the session ends. Then it shows where you and the judge disagreed, and locks the labels you made.

| Topic        | Links                                                                                     |
| ------------ | ----------------------------------------------------------------------------------------- |
| **License**  | [MIT](LICENSE); the page's fonts are under [OFL-1.1](src/arvio/static/fonts/OFL.txt)      |
| **Format**   | [Task folder format, version 1](docs/format.md)                                           |
| **Example**  | [examples/hello](examples/hello), a complete made-up task                                 |
| **Design**   | [v0.1 design and its decisions](.ai/docs/plans/2026-10-18-arvio-v0.1.md)                  |
| **Security** | [Report a vulnerability privately](https://github.com/jboho/arvio/security/advisories/new) |

## How it fits a project

Your project never imports arvio. It writes a task folder:

```
my-task/
  task.toml      the task card: question, choices and what each one means
  items.jsonl    one item per line: what the labeler sees, plus the hidden judge answer
  labels.jsonl   written by arvio, read back by your project
```

The format is in [docs/format.md](docs/format.md). [examples/hello](examples/hello) is a complete made-up task.

## Use

Needs Python 3.11 or newer and [uv](https://docs.astral.sh/uv/).

```sh
uv tool install git+https://github.com/jboho/arvio@v0.1.0
arvio check my-task      # check a task folder; exits 1 and names the file and line on any error
arvio add my-task        # check it and add it to your inbox
arvio                    # open the labeling page for your inbox
arvio report my-task     # compare your labels with the judge; writes report.md and report.json
arvio labels my-task     # print the current label per item as JSON lines, for your own scripts
arvio status             # what is waiting, and how long the next session takes
arvio remove my-task     # take it off the inbox; its files stay
```

To try it, copy `examples/hello` somewhere outside the repo and use the copy, since labeling writes into the task folder.

The page shows one item at a time. Press a choice's key, then Enter to save; ← goes back. The judge's answers appear when you finish the session, and the labels you made are locked from then on.

`arvio report` exits 2 when the task card sets a pass rule (a gate) and the labels don't meet it yet, so a script can stop on it.

The inbox lives in `~/.arvio`. Set `ARVIO_DATA_DIR` to put it elsewhere, and `ARVIO_LABELER` to change the name written with each label (your login name by default).

## Status

Version 0.1.0, and experimental. The tests run on Python 3.11, 3.12 and 3.13 in CI. The page has been checked in a browser at desktop width, by keyboard only, in the light and dark themes. One person has used it so far, for one real labeling pass of 20 items.

### Known gaps

- One labeler per task. There is no agreement between labelers yet.
- Text is shown as plain text, so Markdown stays raw. A highlight has to match the text exactly, so a phrase split across a line break isn't highlighted.
- The page doesn't scroll to the highlight. In a long block you may have to scroll to find it.
- On a phone, reaching the choices takes a long scroll and the header is cut off. Tapping a text box may zoom the page.
- The time estimate is rough until arvio has measured a few of your labels.
- When you disagree with the judge, the results show the judge's raw value, such as "supported (same as Matches)".
- Finish locks every label you made under the current card that isn't locked yet, including ones left from a session you closed early. That is by design, but easy to miss.

## License

MIT. The fonts the page ships with, Space Grotesk and IBM Plex Mono, are under the SIL Open Font License 1.1 ([OFL.txt](src/arvio/static/fonts/OFL.txt)).
