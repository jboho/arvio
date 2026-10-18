# arvio

A small local tool for labeling eval items by hand: one item at a time, with the question and the meaning of each answer on screen, and the judge's answer hidden until the session ends.

*Arvio* is Finnish for "an assessment".

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

## License

MIT. The fonts the page ships with, Space Grotesk and IBM Plex Mono, are under the SIL Open Font License 1.1 ([OFL.txt](src/arvio/static/fonts/OFL.txt)).
