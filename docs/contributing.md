# Contributing

The canonical guide lives in the repository's `CONTRIBUTING.md`; this page is the short
version.

**You do not need an RFID reader.** The emulator is the development target — the entire test
suite and demo run against it.

```console
$ git clone <repo-url> && cd llrpkit
$ python -m venv .venv && source .venv/bin/activate
$ pip install -e ".[dev,docs]"
$ pre-commit install
```

Before pushing, run what CI runs: `ruff check .`, `ruff format --check .`, `mypy`, `pytest`,
and `mkdocs build --strict` if you touched docs. Commits follow Conventional Commits, and
user-visible changes get a `CHANGELOG.md` line under *Unreleased*.
