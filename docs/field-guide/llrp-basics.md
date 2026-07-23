# LLRP in plain English

LLRP — the EPCglobal/GS1 *Low Level Reader Protocol* — is how software talks to
fixed RAIN RFID readers. It is a binary protocol over TCP (port 5084), and its
central idea takes a moment to internalize: **you do not read tags; you describe
reading, and the reader does it.**

## The mental model

The reader owns its radio. Your client never says "read now"; instead it
uploads a *specification* of what reading should look like — which antennas,
which air-protocol settings, when to start and stop, what to report — and then
turns that specification on. The reader executes it autonomously and streams
results back whenever it has them. Everything else in LLRP hangs off this
declarative shape.

The specification for inventory is the **ROSpec** ("reader operation spec"),
and it nests like this:

```text
ROSpec                        who am I, what priority, what state
├── ROBoundarySpec            when does the whole operation start/stop
├── AISpec                    "antenna inventory": the actual reading
│   ├── antenna list          which ports (0 = all)
│   ├── AISpecStopTrigger     duration, GPI, tag-observation, or null
│   └── InventoryParameterSpec
│       └── AntennaConfiguration
│           ├── RFTransmitter        power & channel
│           └── C1G2InventoryCommand session, target, RF mode, Q
│               └── (Impinj) search mode — TagFocus lives here
└── ROReportSpec              what fields to report, how often
    └── (Impinj) content selector — sub-dBm RSSI, phase, TID
```

llrpkit's `build_rospec()` assembles exactly this tree from keyword arguments,
and `Reader.inventory()` wraps the whole lifecycle:

```text
ADD_ROSPEC → ENABLE_ROSPEC → START_ROSPEC → RO_ACCESS_REPORT, RO_ACCESS_REPORT, ...
                                             → STOP_ROSPEC → DELETE_ROSPEC
```

A ROSpec is a little state machine on the reader (`Disabled → Enabled →
Active`), which is why the dance has three steps up and two steps down. llrpkit
always deletes its ROSpec on the way out — a crashed client that leaves an
Active ROSpec behind is a classic source of "why is this reader still
transmitting" confusion.

## Messages and parameters

Everything on the wire is a **message** (10-byte header: version, type, length,
message ID) whose body is a tree of **parameters**. Parameters come in two
encodings: TLV (16-bit type, 16-bit length, nestable) and the compact TV form
(one type byte, fixed layout) used for hot-path report fields like EPC-96 and
peak RSSI. Vendor extensions ride in `CUSTOM_MESSAGE` / custom parameters,
tagged with the vendor's IANA number — Impinj's is 25882, and that is where the
entire Octane feature set lives.

Message IDs correlate requests with responses: your `GET_READER_CAPABILITIES`
with ID 7 is answered by a `..._RESPONSE` with ID 7. Three kinds of traffic
arrive *unsolicited*: `RO_ACCESS_REPORT` (tag data), `READER_EVENT_NOTIFICATION`
(antenna events, exceptions, the connection handshake), and `KEEPALIVE`, which
you must acknowledge or the reader will conclude you are gone. llrpkit's client
handles correlation and keepalives; reports and events surface as queues.

## The connection handshake

A reader accepts one controlling client at a time. On connect it immediately
sends a `ReaderEventNotification` carrying a `ConnectionAttemptEvent`; anything
but *Success* means someone else is already attached, and the socket is useless.
llrpkit turns that into a clear `LLRPConnectionError` — if you see "connection
already exists", find the other client (often a forgotten ItemTest window or a
crashed service).

## Status codes, not exceptions

Every request gets a response containing an `LLRPStatus`; errors are data, not
broken sockets. `M_Success` is 0; anything else carries a code and a
human-readable description written by the reader. llrpkit's `check_status()`
raises `LLRPStatusError` so application code can treat reader-side rejection
like the exception it morally is — but the connection itself stays healthy, and
you can simply try again with a corrected request.

## Where llrpkit sits

The layering mirrors the protocol: `llrpkit.protocol` is the wire (generated
from the LLRP definitions, byte-accurate, fuzz-hardened); `llrpkit.client` is
one TCP connection with framing, correlation, and queues; `llrpkit.reader` is
the API you actually use — capabilities, configuration, and streaming
inventory. If llrpkit's high-level surface is ever missing something, the full
protocol is right there: build any message by hand and `client.transact()` it.
