# Contributing to llrpkit

Thanks for your interest! Contributions of every size are welcome — protocol work, dashboard
polish, docs fixes, and real-world reader reports are all valuable.

## You do not need an RFID reader

This is the most important thing to know. llrpkit ships an LLRP reader **emulator**, and the
entire test suite and demo run against it. If you have hardware, wonderful — the
hardware-in-the-loop suite (`pytest --hil`, arriving with Phase 2) will put it to work — but
every feature in this project can be developed and verified with nothing but Python.

## Development setup

```console
$ git clone <repo-url> && cd llrpkit
$ python -m venv .venv && source .venv/bin/activate
$ pip install -e ".[dev,docs]"
$ pre-commit install
```

Before pushing, run what CI runs:

```console
$ ruff check . && ruff format --check .   # lint + formatting
$ mypy                                    # strict type checking
$ pytest                                  # tests with coverage
$ mkdocs build --strict                   # docs (if you touched them)
```

## Conventions

Commits follow [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`,
`docs:`, `test:`, `ci:`, `chore:` …). Code is formatted by ruff and typed strictly — if mypy
complains, the fix is usually a better type, not a `# type: ignore`. User-visible changes get a
line in `CHANGELOG.md` under *Unreleased*. New protocol code needs tests, ideally including
golden wire-format vectors (hex in, object out, hex back).

## Pull requests

Small, focused PRs review fastest. Open an issue first for anything architectural so we can
agree on direction before you invest time. By contributing you agree your work is licensed
under the project's MIT license.
