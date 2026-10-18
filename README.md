# arvio

A small local tool for labeling eval items by hand: one item at a time, with the question and the meaning of each answer on screen, and the judge's answer hidden until the session ends.

*Arvio* is Finnish for "an assessment".

Work in progress. This version reads and checks task folders and keeps an inbox of them. The labeling page and the agreement report come next.

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
uv tool install git+https://github.com/jboho/arvio
arvio check examples/hello    # check a task folder; exits 1 and names the file and line on any error
arvio add examples/hello      # check it and add it to your inbox
arvio status                  # what is waiting, and how long the next session takes
arvio remove examples/hello   # take it off the inbox; its files stay
```

The inbox lives in `~/.arvio`. Set `ARVIO_DATA_DIR` to put it elsewhere.

## License

MIT
