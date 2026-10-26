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
  <img src="https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey" alt="Platform: macOS and Linux" />
  <img src="https://img.shields.io/badge/runtime%20dependencies-none-brightgreen.svg" alt="No runtime dependencies" />
</p>

<p align="center">
  <sub><em>Arvio</em> is Finnish for "an assessment" · Python standard library only · runs on your own machine</sub>
</p>

---

## Overview

**arvio** is a small local tool for labeling eval items by hand. It shows one item at a time, with the question and the meaning of each answer on screen, and keeps the judge's answer hidden until the session ends. Then it shows where you and the judge disagreed, and locks the labels you made.

| Topic        | Links                                                                                      |
| ------------ | ------------------------------------------------------------------------------------------ |
| **License**  | [MIT](LICENSE); the page's fonts are under [OFL-1.1](src/arvio/static/fonts/OFL.txt)       |
| **Format**   | [Task folder format, version 1](docs/format.md)                                            |
| **Guide**    | [Add a task, step by step](docs/add-a-task.md)                                             |
| **Example**  | [examples/hello](examples/hello), a complete made-up task                                  |
| **Security** | [Report a vulnerability privately](https://github.com/jboho/arvio/security/advisories/new) |

## Status

Experimental. The tests run on Linux in CI, on Python 3.11, 3.12 and 3.13. The page has been checked in a Chromium-based browser on macOS at desktop width, by keyboard only, in the light and dark themes. One person has used it so far, for one real labeling pass of 20 items. See [Known gaps](#known-gaps) before you rely on it.

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

New to arvio? [Add a task](docs/add-a-task.md) walks you from a task folder to your first label in five steps, and covers the common errors.

Needs macOS or Linux, Python 3.11 or newer, and [uv](https://docs.astral.sh/uv/).

```sh
uv tool install git+https://github.com/jboho/arvio@v0.2.0
arvio check my-task      # check a task folder; exits 1 and names the file and line on any error
arvio add my-task        # check it and add it to your inbox
arvio add --import my-task   # same, from a copy arvio keeps in ~/.arvio/tasks, so the labels outlive the project's checkout
arvio archive my-task    # put a task away on the Archived tab; labels stay where they are
arvio unarchive my-task  # move it back to the active list
arvio                    # open the labeling page for your inbox
arvio report my-task     # compare your labels with the judge; writes report.md and report.json
arvio labels my-task     # print the current label per item as JSON lines, for your own scripts
arvio status             # what is waiting, and how long the next session takes (--archived lists the archived ones)
arvio remove my-task     # take it off the inbox; its files stay
arvio man                # read the manual; `arvio man --install` puts it where plain `man arvio` finds it
```

To try it, copy `examples/hello` somewhere outside the repo and use the copy, since labeling writes into the task folder.

The page shows one item at a time. Press a choice's key, then Enter to save; ← goes back. The judge's answers appear when you finish the session. Finish locks every label under the current card that isn't locked yet, including ones left from a session you closed early.

`arvio report` exits 2 when the task card sets a pass rule (a gate) and the labels don't meet it yet, so a script can stop on it.

`arvio man` shows the manual for the command, and `arvio man task` the one for the task folder format. To make plain `man arvio` and `man arvio-task` work, run `arvio man --install` once, and again after you upgrade arvio. It copies both pages into `~/.local/share/man` (or `$XDG_DATA_HOME/man`), which `man` searches when that folder exists and `~/.local/bin` is on your `PATH`. That search rule was checked on macOS only. On another system, or to use another folder, run `arvio man --install --dir DIR` and add `DIR` to your `MANPATH`.

The inbox and the imported tasks live in `~/.arvio`. The page has an Active and an Archived tab, and each task has a button to move it between them. Set `ARVIO_DATA_DIR` to put it elsewhere, and `ARVIO_LABELER` to change the name written with each label (your login name by default).

## Known gaps

- Windows isn't supported: arvio locks files with `fcntl`, which Windows doesn't have.
- One labeler per task. arvio writes each label's name but doesn't keep labels apart by it: a second person's label replaces the first one, and items someone else labeled don't show as waiting. There is no agreement score between labelers.
- Text is shown as plain text, so Markdown stays raw.
- A highlight must be an exact part of its block's text, spaces and line breaks included. `arvio check` rejects an item whose highlight doesn't match, and then the whole task fails to load, so a project's exporter has to drop or fix a phrase that differs only in spacing.
- The page doesn't scroll to the highlight. In a long block you may have to scroll to find it, or open a folded block.
- On a phone, reaching the choices takes a long scroll and the header is cut off. In Safari on iPhone, tapping a text box zooms the page, because the boxes use 13 px text.
- The time estimate uses the card's `seconds_per_item` until you have labeled 3 items in that task.
- The results show the judge's answer in the judge's own words, with the choice it maps to in brackets, such as "supported (same as Matches)", rather than as one of your choices.

## License

MIT. The fonts the page ships with, Space Grotesk and IBM Plex Mono, are under the SIL Open Font License 1.1 ([OFL.txt](src/arvio/static/fonts/OFL.txt)).
