# Changelog

All notable changes to llrpkit are documented in this file. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Web dashboard (Phase 4), behind the ``dashboard`` extra: a FastAPI +
  WebSocket app with four views — live tag stream with stat tiles and a
  rolling read-rate chart, per-antenna health cards with sparklines and the
  alert log, a tuning workbench (session/search-mode/RF-mode/power with the
  curated guidance inline, mode suggestions, and settings profiles), and
  reader management. The UI is dependency-free vanilla JS with hand-rolled
  SVG charts — no build step, static files ship inside the wheel.
- `llrpkit demo`: emulator + dashboard + a running inventory in one command —
  the complete zero-hardware experience. `llrpkit dashboard` runs the same
  app for real readers (binds to localhost by default).

- Tuning and health layer (Phase 3). `llrpkit.modes`: curated knowledge for
  the Impinj mode identifiers (fixed 0-5 and the AutoSet families) joined at
  runtime with the RFModeTable the connected reader actually reports, plus a
  transparent `suggest_mode()`; `llrpkit modes` shows it all on the CLI.
  `llrpkit.health`: per-antenna rolling statistics, antenna
  connect/disconnect event handling, and once-per-incident quiet-port
  alerts. `llrpkit.profiles`: named JSON inventory settings profiles.
  `Reader` grows `set_keepalive()`, `get_temperature()` (Octane),
  `events()`, and `annotated_modes()`.
- Emulator behavioral realism: the RF mode index now scales the synthetic
  read rate, TagFocus (search mode 3, session 1) suppresses the population
  after first sightings the way the real feature does, antenna fault
  injection emits proper `AntennaEvent`s and silences the port, periodic
  keepalives honor `KeepaliveSpec`, and the Octane temperature is served.

- Asyncio client stack (Phase 2): `LLRPClient` (stream framing, message-ID
  correlation, automatic keepalive acks, report/event queues, strict status
  checking) and the high-level `Reader` facade — connect, parsed
  capabilities (transmit power table, RF mode table, frequency info), the
  Impinj Octane extensions handshake, and `reader.inventory(...)`, an async
  stream of flattened `TagReport` objects with unit conversion (sub-dBm
  RSSI, phase in degrees, Doppler in Hz, serialized TID bytes). ROSpecs are
  created on entry and always stopped and deleted on exit.
- Reader emulator (`llrpkit.emulator.LLRPEmulator`): an in-process
  Impinj-flavored LLRP reader — connection-attempt handshake with proper
  second-client refusal, capabilities, the full ROSpec lifecycle, Octane
  extensions awareness, and synthetic tag reports honoring the requested
  report content. Powers the test suite, CI, and zero-hardware demos.
- CLI: `llrpkit inventory` (live tag stream with search-mode/power/session
  tuning flags), `llrpkit capabilities`, and `llrpkit emulate`.

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
