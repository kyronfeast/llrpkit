# Changelog

All notable changes to llrpkit are documented in this file. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Project scaffold (Phase 0): hatchling packaging with a `src` layout, ruff + strict mypy +
  pytest/coverage tooling, pre-commit hooks, GitHub Actions CI (lint, test matrix across
  Python 3.11–3.14 on Linux/macOS/Windows, build, docs), docs-deploy and PyPI
  trusted-publishing release workflows, and the mkdocs-material documentation skeleton.
- Stable core surface: the `LLRPError` exception hierarchy, protocol constants
  (`LLRP_PORT`, `LLRP_TLS_PORT`, `MESSAGE_HEADER_LEN`, `IMPINJ_PEN`, `LLRPVersion`),
  and the `llrpkit` CLI entry point with `version`.
