# Pre-release QA report — llrpkit v0.1.0

**Date:** 2026-07-23 · **Scope:** full stack (protocol, client, reader, emulator, tuning/health, dashboard, packaging) · **Method:** adversarial production-simulation testing · **Verdict:** release-ready after 8 fixes (1 critical, 2 high, 4 medium, 1 low)

## Method

The library was attacked the way production attacks it, not the way unit tests
flatter it: long-lived connections that outlive their consumers, streams
cancelled at the worst possible moment, hostile bytes on the LLRP port,
hostile strings from the reader rendered in the browser, sustained 300+
reads/s load, rapid dashboard churn (start/stop/reconfigure/remove loops),
concurrent transactions, and repeated full-suite runs to surface
nondeterminism. Every defect found was first pinned with a failing regression
test, then fixed, and the test suite retains those tests permanently
(`tests/test_hardening.py`, `tests/test_soak.py`).

Environment: Python 3.11.15, Linux x86_64, all traffic against the
in-package `LLRPEmulator` (no hardware in the loop).

## Findings summary

| ID | Severity | Component | Finding | Outcome |
|----|----------|-----------|---------|---------|
| QA-9 | **Critical** | client/reader streams | Task cancellation silently swallowed when a queue is hot (`asyncio.wait_for` on 3.11) → uncancellable inventory stream, deadlocked `stop` | Fixed |
| SEC-1 | **High** | dashboard UI | Stored XSS: reader- and user-supplied strings interpolated into `innerHTML` unescaped | Fixed |
| QA-1 | **High** | `LLRPClient` | Lifecycle lied: `connected` stayed `True` after close; client unusable after close or refused connect | Fixed |
| QA-2 | Medium | `LLRPClient` | Unbounded report/event queues — slow memory leak on any long-lived connection | Fixed |
| QA-3 | Medium | `health` | Read-rate metric silently capped at 256 reads/s per antenna | Fixed |
| QA-7 | Medium | emulator | Crashed report task treated as alive — reporting silently dead for the session | Fixed |
| QA-10 | Medium | `LLRPClient` | Cancelling `connect()` leaked a half-open transport; `close()` not cancellation-safe | Fixed |
| QA-8 | Low | `inventory` | Invalid `session`/`tag_population` surfaced as cryptic bit-level encode errors | Fixed |
| QA-4 | Medium | dashboard registry | Verified: cancellation-driven stop must still DELETE the ROSpec on the reader; `remove()` must survive close errors (one robustness fix) | Fixed/verified |
| QA-5 | Low | emulator | Verified: garbage/port-scanner bytes on the LLRP port neither wedge the emulator nor poison the next session | No defect |
| QA-6 | Low | `LLRPClient` | Verified: 50 concurrent `transact()` calls correlate responses correctly by message ID | No defect |
| QA-11 | **High** | `mqtt` bridge | The QA-9 swallow again, one dependency down: aiomqtt acknowledges QoS>0 publishes via `asyncio.wait_for`, so a PUBACK racing `Task.cancel()` made the bridge uncancellable | Fixed |
| INFRA-1 | — | test infra | Intermittent full-suite hang initially misattributed to Starlette's `TestClient` portal; true cause was QA-9 | Root-caused |

## Critical finding in detail — QA-9, the cancellation swallow

**Symptom.** Roughly 1 in 4 full-suite runs hung forever in
`test_inventory_start_stats_and_stop` (dashboard start → reads observed →
stop). A watchdog stack dump showed the event loop *alive* — actively
delivering socket data — while the test never progressed. In production
terms: clicking **Stop** on the dashboard while tags were flowing could hang
that reader's control path permanently, with the tag stream still running.

**Root cause.** `Reader.inventory()` and `Reader.events()` polled their
queues with `asyncio.wait_for(queue.get(), 0.25)`. Python 3.11's `wait_for`
has a known race (python/cpython#86296): when `Task.cancel()` lands while
the awaited future has already completed, `wait_for` catches the
`CancelledError` internally and **returns the value instead of re-raising**.
The cancellation is consumed. With reports arriving at 300/s the queue is
frequently "hot," so `stop_inventory()`'s `task.cancel()` could be eaten by
a report that arrived in the same event-loop tick — the stream looped on,
un-cancelled, and `await task` deadlocked. A 20-line script reproduces the
swallow deterministically on 3.11.15 (put + cancel in the same tick).

**Fix.** Every bounded wait in llrpkit now uses `asyncio.timeout(...)`
(3.11+), which distinguishes its own expiry from external cancellation and
always re-raises the latter — in the inventory stream, the events stream,
`transact()` (whose bound now also covers `drain()`, so a wedged socket
cannot stall a transaction indefinitely), both connect phases, and the
emulator's keepalive helper. `asyncio.wait_for` no longer appears anywhere
in `src/`.

**Evidence.** Regression tests
`test_inventory_cancel_wins_even_with_a_report_already_queued` and
`test_events_generator_cancel_wins_even_with_an_event_queued` stage the
exact interleaving; both fail on the pre-fix code with "cancellation was
swallowed; inventory stream still running" and pass on the fix. The full
suite then ran **10/10 consecutive clean runs** (previously 2/8 hung).

**Honest note (INFRA-1).** This hang was initially blamed on Starlette
`TestClient`'s thread-portal, because the first watchdog dump showed the
portal wedged with no llrpkit frames on any stack. The dashboard tests were
rewritten to run in-loop (`httpx.ASGITransport`) and against a real uvicorn
server with a real WebSocket client — and the hang *persisted at the same
test*, which is what exposed the real defect. The rewrite was kept anyway:
it is higher-fidelity (it exercises uvicorn's WebSocket backend, whose
missing dependency once broke the live demo while portal-based tests stayed
green) and the diagnosis story is preserved in the test module docstrings.

## Security finding — SEC-1, dashboard XSS

Reader-controlled strings (firmware version, error descriptions), tag EPCs,
and user-supplied names (hosts, profiles, mode names) were interpolated into
`innerHTML` templates unescaped. A hostile or compromised reader answering
`GET_READER_CAPABILITIES` with `<img src=x onerror=...>` in its firmware
string would execute script in the operator's browser. All dynamic strings
now pass through an HTML-escape helper before rendering. Verified in a real
Chromium session: the emulator was given the firmware string
`evil-fw <img src=x onerror=window.__xss=1> & 'quotes'`, the dashboard
rendered it inert, and `window.__xss` remained unset.

**Sequel — QA-11, the same swallow in a dependency.** The MQTT bridge added
after this QA pass was developed under the same discipline, and its
cancel-mid-flood regression test immediately caught the QA-9 failure mode
*inside aiomqtt*: QoS>0 publish acknowledgements go through
`asyncio.wait_for`, so a PUBACK arriving in the same event-loop tick as
`Task.cancel()` consumed the cancellation and the bridge ran on forever.
Third-party code cannot be patched, so the bridge resurfaces swallowed
cancels instead: a consumed cancel leaves `Task.cancelling() > 0`, which the
bridge checks after every publish and converts back into a real
`CancelledError`. The test went from failing roughly two runs in three to
5/5 clean.

## Remaining findings in brief

**QA-1 (high).** `close()` never cleared the transport, so `connected`
stayed `True`, `transact()` wrote into a dead socket, and a refused or
failed connect left the object permanently claiming "already connected."
`_shutdown()` now clears the transport, and `connect()` resets
per-connection state, making the client honestly reusable across
close/refuse/retry cycles.

**QA-2 (medium).** `client.reports`/`client.events` grew without bound if a
consumer stalled — a slow leak on every long-lived connection. Both queues
are now bounded (defaults 65 536 / 1 024, constructor-tunable) with
drop-oldest semantics and `dropped_reports`/`dropped_events` counters, so
back-pressure is visible instead of fatal.

**QA-3 (medium).** `AntennaHealth` kept 512 timestamps over a 2 s window,
silently capping the reported rate at 256 reads/s per port — real R700
deployments exceed that. The buffer is sized (4 096) for ~2 000 reads/s.

**QA-7 (medium).** The emulator's `_sync_reporting` treated a
completed-but-still-referenced task as alive, so a crashed report loop
silently disabled reporting for the rest of the session. It now checks
`task.done()` and restarts the loop on the next state change.

**QA-10 (medium).** Cancelling `connect()` while waiting for the reader's
`ConnectionAttemptEvent` skipped the abort path: socket and read task
leaked, and `connected` reported `True` on a half-open client. Any failure
or cancellation in the handshake now runs the abort path, and `close()`
releases the transport even if cancelled mid-goodbye.

**QA-8 (low).** `build_rospec(session=5)` failed deep in the bit-packer;
`session` and `tag_population` are now validated at the API boundary with
plain `ValueError`s.

**QA-4 (medium, one fix + one verification).** The dashboard stops streams
by task cancellation; a regression test proves the generator's `finally`
still STOPs and DELETEs the ROSpec on the reader through that path (no
orphaned ROSpecs). Fixed alongside it: `ReaderRegistry.remove()` now
publishes the updated roster even when the LLRP goodbye fails, so a dead
reader cannot desynchronize connected UIs.

## Verification

- **Stability:** 10/10 consecutive full-suite runs green under a 90 s
  per-test watchdog (`pytest-timeout`), which remains in CI so any future
  hang fails loudly with stack dumps instead of stalling.
- **Soak:** 25 rapid inventory reconfigurations, 20 hub subscribe/unsubscribe
  cycles, and 6 reader remove/re-add cycles leak zero asyncio tasks against
  baseline and leave no Active ROSpec (`tests/test_soak.py`).
- **Suite:** 327 tests; coverage 92% (fail-under 90, generated protocol
  modules excluded); `ruff format --check`, `ruff check`, and strict `mypy`
  all clean; `codegen --check` byte-identical.
- **Packaging:** wheel + sdist build and `twine check` clean; fresh-venv
  install smoke-tested — bare `pip install llrpkit` importing the dashboard
  produces a friendly "install llrpkit[dashboard]" hint, and the extra runs
  the demo end-to-end.

## Residual risks and notes

- Drop-oldest queue semantics mean a stalled consumer loses the *oldest*
  reports by design; the counters make this observable. Applications that
  must not lose reads should consume `reports` promptly or raise the bound.
- The emulator intentionally accepts one client at a time (matching reader
  behavior); tests that reconnect immediately allow it ~50 ms to free the
  slot.
- All findings were reproduced against the emulator; behavior against
  physical R700 hardware (timing, firmware quirks) remains to be validated
  in the field, which the field guide's onboarding chapter supports.
