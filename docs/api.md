# API tour

A guided walk through the layers, top down. Everything shown here is typed,
and the docstrings in the source go deeper than this page.

## `llrpkit` — the front door

```python
from llrpkit import Reader, TagReport, InventoryProfile, HealthMonitor

async with Reader("192.168.1.10") as reader:
    caps = reader.capabilities            # powers, RF modes, antennas — parsed
    modes = reader.annotated_modes()      # RFModeTable + curated guidance
    await reader.set_keepalive(5000)
    print(await reader.get_temperature()) # Octane extension

    async for tag in reader.inventory(
        antennas=(1, 2), session=1, search_mode=3,   # TagFocus
        tx_power_dbm=27.0, include_phase=True, include_tid=True,
    ):
        print(tag.epc_hex, tag.antenna, tag.rssi_dbm, tag.phase_deg)
```

`Reader.inventory()` is an async generator: the ROSpec is created when
iteration starts and always stopped and deleted when it ends, however it ends.
`TagReport` is the flat, unit-converted row (dBm, degrees, Hz, TID bytes).
`InventoryProfile` serializes a settings bundle to JSON;
`HealthMonitor` turns reads plus reader events into per-port statistics and
once-per-incident quiet-port alerts. `llrpkit.modes.suggest_mode()` picks a
starting mode from what the reader actually offers, with its reasoning.

## `llrpkit.client` — one connection, no opinions

```python
from llrpkit.client import LLRPClient, check_status
from llrpkit.protocol import messages

async with LLRPClient("192.168.1.10") as client:
    response = check_status(await client.transact(messages.GET_ROSPECS()))
    report = await client.reports.get()      # unsolicited RO_ACCESS_REPORTs
    event = await client.events.get()        # notifications & everything else
```

Framing, message-ID correlation, automatic keepalive acks, and clean teardown.
Anything llrpkit's high-level API doesn't expose yet, you can do here with raw
messages.

## `llrpkit.protocol` — the wire

```python
from llrpkit.protocol import decode_message, enums, impinj, messages, params

frame = messages.KEEPALIVE_ACK().to_bytes(message_id=7)
msg = decode_message(frame)
p = impinj.ImpinjInventorySearchMode(inventory_search_mode=3)
```

Every LLRP 1.0.1 message, parameter, and enumeration, plus the Impinj Octane
extension set (PEN 25882), generated from definition files and verified by
hand-computed golden vectors. Unknown messages and parameters decode into
`Unknown*` carriers and re-encode byte-identically, so foreign extensions
survive a round-trip. The codec never raises anything but
`MessageDecodeError` on hostile input — it is fuzzed in CI.

## `llrpkit.emulator` — the reader you always have

```python
from llrpkit.emulator import EmulatedTag, LLRPEmulator

async with LLRPEmulator(tags=[EmulatedTag(epc=b"\xe2" + b"\x00" * 11)]) as emu:
    reader = Reader("127.0.0.1", emu.port)
    ...
    await emu.set_antenna_connected(2, False)   # fault injection
```

A wire-faithful Impinj-flavored reader: capabilities, ROSpec lifecycle, Octane
handshake, mode-dependent read rates, TagFocus suppression, antenna events,
keepalives, temperature. It is the test rig for this project and works just as
well as one for yours.

## Tag access: read, write, re-label, kill

```python
result = await reader.read_memory(bank="user", word_count=4, target_epc=epc)
result.ok, result.data                       # True, b'...'
await reader.write_memory(bank="user", data="cafebabe", target_epc=epc)
await reader.write_epc(new_epc, target_epc=old_epc)   # same length required
await reader.kill_tag(kill_password=0x2A2A2A2A, target_epc=epc)  # permanent!
```

The full Gen2 access surface via the AccessSpec lifecycle: banks by name
(`reserved`/`epc`/`tid`/`user`), EPC-targeted with a one-shot stop trigger,
results parsed into a typed `AccessResult` whose `status` is the reader's
own result name (`"Tag_Memory_Locked_Error"` and friends). Always pass
`target_epc` with more than one tag in the field. CLI: `llrpkit read`,
`llrpkit write`, `llrpkit write-epc`.

## Filters, GPIO, presence, decoding

```python
async for tag in reader.inventory(epc_filter="e280", filter_action="include"):
    ...                                       # the reader itself pre-selects

state = await reader.get_gpio()               # {1: "low", ...}, {1: False, ...}
await reader.set_gpo(2, True)                 # stack light on

from llrpkit import PresenceTracker, decode_epc, ticked_stream

tracker = PresenceTracker(depart_after=2.0)
async with contextlib.aclosing(ticked_stream(reader.inventory(session=1))) as ticked:
    async for tag in ticked:                  # TagReport, or None on quiet ticks
        for event in (*(tracker.observe(tag) if tag else ()), *tracker.check()):
            print(event.kind, event.epc_hex, event.dwell_s)

decode_epc("3074257bf7194e4000001a85").gs1    # "(01) 80614141123458 (21) 6789"
```

Select filters run **on the reader** (C1G2 Select), so unwanted tags never
cost airtime. GPI edges arrive through `reader.events()` as `GPIEvent`
notifications. `PresenceTracker` turns the read firehose into arrive/depart
edges (`min_reads` debounces strays; `depart_after` defines gone), and
`llrpkit.epc` decodes GS1 schemes — SGTIN-96 through GID-96 — into GTINs,
SSCCs, and pure-identity URIs.

## Capture and surveys

```python
from llrpkit import TagWriter, sweep

with TagWriter("dock.csv") as writer:         # or .jsonl
    async for tag in reader.inventory(duration=30):
        writer.write(tag)

points = await sweep(reader, powers_dbm=[15, 20, 25, 30], seconds=3)
best = max(points, key=lambda p: (p.unique, p.reads_per_sec))
```

`TagWriter` exports reads with GS1 decode columns included; `sweep()` is
the field guide's tuning methodology as a function — reads/s *and* unique
count per power x mode combination. CLI: `llrpkit inventory --output` and
`llrpkit sweep`.

## `llrpkit.dashboard` — the web layer

```python
from llrpkit.dashboard import create_app, create_demo_app  # needs [dashboard]
```

`create_app()` returns the FastAPI application behind `llrpkit dashboard`
(REST + WebSocket over a `ReaderRegistry`); `create_demo_app()` is the same
app pre-wired to an emulator — embed either in your own ASGI stack if the CLI
entry points don't fit.
