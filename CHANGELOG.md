# Changelog

All notable changes to llrpkit are documented in this file. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **R700 mode-number decoding** (`llrpkit.modes`): `generic_description` now
  decodes Impinj's documented R700 mode numbering — three-digit static modes
  (region + Miller), Gen2 AutoSet families (`11xx`/`12xx`/`13xx`), Gen2X static
  (`4xxx`), and Gen2X AutoSet (`5xxx`) — and `is_autoset_mode_id` classifies
  them correctly (Gen2X `4xxx` static modes are no longer mis-flagged as
  AutoSet). Sourced from the Impinj R700 *Reader Modes* application note v2.1.

### Docs

- Field guide, R700 onboarding: an **antenna hub** section (up to 32 antennas via
  R702 hubs — activation with `config feature enable anthub`, 1–32 port
  renumbering, ~1.7 dB insertion loss); `config rfid llrp connclose` to recover a
  stuck LLRP client slot; and a clarified TLS note (the reader supports 5085/TLS
  and reader-initiated connections; llrpkit's client is plaintext/inbound today).
- Field guide, reader modes: the **R700 mode-number scheme and Gen2X** (M800-only)
  explained.
- Confirmed dynamic antenna handling works for hub-sized readers with a test
  (`antenna_count=32` → 32 antennas, inventory on a hub-range antenna).

## [0.2.0] - 2026-08-25

Complete reader control from the dashboard, and host-side ignore policies.

### Added

- **Reader ignore policy** (`llrpkit.policy`): keep only the tags a reader
  should see, enforced **host-side** so ignored tags never reach MQTT, a
  webhook, or an ERP. An `ItemCatalog` classifies each tag into a business
  category (match by exact EPC, GS1 GTIN, GS1 company prefix, or EPC
  prefix — the GS1 forms come from `decode_epc`, so a catalog is a product
  export); an `AntennaPolicy` gives each port an allow/deny category set
  with an optional RSSI floor; `ReaderPolicy.evaluate()` decides keep/ignore
  and tallies every drop by antenna, category, and reason. JSON is the
  config format (`examples/policy.example.json`).
- `Reader.inventory(policy=...)` filters the stream before yielding, so the
  dashboard, MQTT bridge, webhook sink, and capture all see only survivors,
  and kept tags carry their `category`. CLI `llrpkit inventory --policy
  FILE` (flows to every sink) with a per-run drop summary.
- **Dashboard Control tab**: an in-browser policy editor (per-antenna
  allow/deny rules, a catalog box, global floors, live drop counters that
  update in real time), tag operations (read/write memory, rewrite EPC),
  a GPIO panel (output toggles + live input states), and a coverage-sweep
  workbench. New REST under `/api/readers/{id}/`: `policy` (GET/PUT/DELETE),
  `gpio` (+ `/output`, `/input`), `tag/{read,write,write-epc}` (409 while an
  inventory runs — tag access needs the reader idle), and `sweep`. The live
  tag table gains Category and GS1 Identity columns.


## [0.1.0] - 2026-07-23

The first release: the complete stack, built and verified entirely against the
in-package emulator, then hardened by an adversarial pre-release QA pass
(findings and evidence in `QA_REPORT.md`).

### Fixed (pre-release QA)

- **Task cancellation could be silently swallowed** by Python 3.11's
  `asyncio.wait_for` when a report/event was already queued, leaving an
  inventory stream running forever and deadlocking `stop`. All bounded waits
  now use `asyncio.timeout`, which always re-raises external cancellation;
  `transact()`'s bound additionally covers `drain()` so a wedged socket
  cannot stall a transaction indefinitely. (QA-9)
- **Dashboard XSS:** reader-supplied strings (firmware, error text), tag
  EPCs, and user-supplied names are now HTML-escaped before rendering;
  verified in-browser against a hostile firmware string. (SEC-1)
- `LLRPClient` lifecycle is now truthful and reusable: `connected` goes
  `False` after `close()`, and the same client object can reconnect after a
  clean close, a refused connection, or a failed handshake. Cancelling
  `connect()` no longer leaks a half-open transport, and `close()` releases
  the transport even if cancelled mid-goodbye. (QA-1, QA-10)
- `client.reports`/`client.events` are bounded queues (constructor-tunable)
  with drop-oldest semantics and `dropped_reports`/`dropped_events`
  counters, instead of leaking memory when a consumer stalls. (QA-2)
- Antenna health read-rate no longer silently caps at 256 reads/s per port
  (timestamp buffer sized for ~2 000 reads/s). (QA-3)
- The emulator restarts its report loop if the task died, instead of
  treating a crashed task as alive. (QA-7)
- `build_rospec()` rejects invalid `session`/`tag_population` with clear
  `ValueError`s at the API boundary. (QA-8)
- `ReaderRegistry.remove()` publishes the updated roster even when the LLRP
  goodbye fails. (QA-4)
- Test infrastructure: dashboard tests run in-loop via `httpx.ASGITransport`
  plus one real uvicorn + real-WebSocket end-to-end test (no thread-portal
  `TestClient`); new hardening and soak suites pin every QA finding; a 90 s
  per-test watchdog turns any future hang into a stack dump.

### Added

- **Webhook delivery**, behind the ``webhook`` extra:
  `llrpkit.webhook.WebhookSink` / `llrpkit inventory --webhook URL
  --webhook-token T [--webhook-tags]` POSTs batched JSON straight to an
  HTTP endpoint (one consumer, no broker): body `{reader, token, events}`,
  uniform entries with `epc` the only required key, batches ≤500,
  200/403/400 semantics with bounded retry buffering. Tested against a
  real in-process receiver implementing the contract.
- **Pinned wire schemas** for every downstream surface — MQTT
  `{base}/tags` and `{base}/events` payloads and the webhook contract —
  documented in `docs/integration.md` and locked by schema tests so field
  names cannot drift under a consumer.
- **Tag select filters**: `epc_filter`/`filter_action` on
  `Reader.inventory()`/`build_rospec()` (C1G2 Select, include or exclude,
  retargetable via `filter_mb`/`filter_pointer`), CLI `--filter-epc
  --filter-action`, dashboard settings fields, and bit-accurate emulator
  enforcement.
- **Gen2 tag memory access** via the AccessSpec lifecycle:
  `Reader.read_memory()/write_memory()/write_epc()/kill_tag()` with named
  banks, EPC targeting, access/kill passwords, and typed `AccessResult`s;
  CLI `llrpkit read`, `llrpkit write`, `llrpkit write-epc`. The emulator
  models per-tag memory banks, passwords, EPC re-labeling, kill, overrun
  and locked-bank results, and the full ACCESSSPEC message set.
- **GPIO**: `Reader.get_gpio()/set_gpo()/set_gpi_enabled()`, GPI edge
  events through `reader.events()`, `llrpkit gpio` command, and an
  emulator GPIO model with stimulus injection.
- **GS1 EPC decoding** (`llrpkit.epc`): SGTIN-96, SSCC-96, SGLN-96,
  GRAI-96, GIAI-96, and GID-96 to tag URIs, pure-identity URIs, GTIN-14 /
  SSCC-18 / GLN-13 with computed check digits, and GS1 element strings —
  anchored on the Tag Data Standard's canonical vector. CLI `llrpkit
  decode` and `llrpkit inventory --decode`.
- **Presence events** (`llrpkit.presence`): `PresenceTracker` arrive /
  depart edges with dwell times, stray-read debounce, and a
  `ticked_stream()` helper that keeps the clock running through quiet
  fields; CLI `--events --depart-after`, MQTT `{base}/events` via
  `MQTTBridge(publish_events=True)` / `--mqtt-events`.
- **Capture**: `llrpkit.capture.TagWriter` to CSV/JSONL with GS1 decode
  columns; CLI `llrpkit inventory --output FILE`.
- **RF surveys**: `llrpkit.survey.sweep()` measures reads/s and unique
  count per power x mode combination; CLI `llrpkit sweep`. The emulator is
  power-responsive (rate scaling plus a weak-tag energizing threshold), so
  surveys show real coverage differences with zero hardware.
- **Profiles CLI**: `llrpkit profile save/list/show/delete` and `llrpkit
  inventory --profile NAME`, sharing the dashboard's profile store
  (`LLRPKIT_PROFILE_DIR` overrides the location).
- `docs/comparison.md`: an honest feature map against sllurp, the Octane
  SDK, ItemTest, and the Impinj IoT Device Interface.
- MQTT bridge, behind the ``mqtt`` extra (`llrpkit.mqtt.MQTTBridge` and
  `llrpkit inventory --mqtt-broker`): publishes one JSON message per tag
  read to `{base}/tags` and a retained online/offline availability status
  to `{base}/status`, registered as an MQTT Last Will so subscribers learn
  of an ungraceful death from the broker itself. The reader stays in LLRP
  mode — full ROSpec/RF-mode/TagFocus control — while reads fan out over
  MQTT. Verified against a real Mosquitto broker in the test suite
  (`examples/mqtt_bridge.py` shows the library pattern), including a
  regression test for a cancellation-swallow in the MQTT client dependency
  (QA-11 in `QA_REPORT.md`).
- Field guide documentation: LLRP in plain English, sessions and targets,
  reader modes, TagFocus and serialized TID, antenna placement and health,
  and R700 onboarding with interface-switch steps verified against Impinj's
  R700 Installation and Operations Guide (v8.1.7).
- Five runnable examples (`examples/`), each exercised against the emulator
  in CI: `read_tags.py`, `tagfocus_dock_door.py`, `mode_shootout.py`,
  `antenna_watch.py`, `mqtt_bridge.py`. An API tour page and expanded docs
  navigation.
- `RELEASING.md`: the push/Pages/PyPI trusted-publishing runbook.

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
