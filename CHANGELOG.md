# Changelog

All notable changes to llrpkit are documented in this file. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Wire protocol (Phase 1): a hand-written, bit-accurate LLRP codec
  (`llrpkit.protocol.codec`) with strict bounds checking, plus code-generated
  message/parameter/enumeration classes for all of LLRP 1.0.1 (39 messages,
  107 parameters, 42 enumerations) driven by the vendored LLRP Toolkit
  definitions. Unknown messages and parameters are preserved for
  forward-compatible round-trips.
- Impinj Octane LLRP extensions (`llrpkit.protocol.impinj`): the
  `IMPINJ_ENABLE_EXTENSIONS`/`SAVE_SETTINGS` handshake, inventory search
  modes (including TagFocus), `ImpinjTagReportContentSelector` with
  serialized TID / RF phase / peak RSSI / Doppler report content, frequency
  and duty-cycle configuration, reader temperature, GPI/GPO extensions, and
  antenna-hub parameters — 32 parameters, 4 messages, 18 enumerations under
  vendor PEN 25882, defined in llrpkit's own `codegen/impinj.xml`.
- `codegen/generate.py`: deterministic generator; CI verifies committed
  modules match the definitions byte-for-byte (`--check`).
- Protocol test suite: hand-computed golden wire vectors, hypothesis
  round-trip and fuzz properties (the decoder never raises anything but
  `MessageDecodeError` on arbitrary input), a full default-construction
  sweep across every generated class, and codec edge-case coverage.

- Project scaffold (Phase 0): hatchling packaging with a `src` layout, ruff + strict mypy +
  pytest/coverage tooling, pre-commit hooks, GitHub Actions CI (lint, test matrix across
  Python 3.11–3.14 on Linux/macOS/Windows, build, docs), docs-deploy and PyPI
  trusted-publishing release workflows, and the mkdocs-material documentation skeleton.
- Stable core surface: the `LLRPError` exception hierarchy, protocol constants
  (`LLRP_PORT`, `LLRP_TLS_PORT`, `MESSAGE_HEADER_LEN`, `IMPINJ_PEN`, `LLRPVersion`),
  and the `llrpkit` CLI entry point with `version`.
