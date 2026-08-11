# How llrpkit compares

An honest map of the RAIN RFID tooling landscape as of mid-2026, so you can
pick the right tool — including when that is not llrpkit.

## The landscape

**[sllurp](https://github.com/sllurp/sllurp)** is the long-standing pure-Python
LLRP client (GPL-licensed, Twisted/threading heritage). It proved Python
belongs in this space and supports a broad set of readers (Speedway, Zebra
FX series, Motorola). llrpkit differs in being asyncio-native and fully
typed, Apache-2.0 licensed, generated from the LLRP definitions rather than
hand-maintained, R700-first in its Impinj extension coverage, and in shipping
an emulator, a dashboard, and written field documentation alongside the
protocol code.

**Impinj Octane SDK** (.NET/Java) is the vendor's official library — the
reference for what a full-featured client looks like: tag operations,
filters, GPIO, keepalives, connection recovery. If you are building on .NET
or Java, use it. llrpkit exists because there is no Python equivalent.

**Impinj ItemTest** is the vendor's desktop testing tool — inventory
visualization, settings experiments, capture to file. llrpkit's dashboard
and capture cover the same workflow needs in a browser, scriptably, and
against the emulator when no reader is at hand.

**Impinj IoT Device Interface** is the R700's REST/MQTT-native control
plane — excellent for streaming integration, but mutually exclusive with
LLRP on the reader, and it exposes presets rather than the full
ROSpec/RF-mode control surface. llrpkit's answer is the MQTT bridge and
presence events: LLRP-grade control with IoT-grade distribution.

## Feature map

| Capability | llrpkit | sllurp | Octane SDK | IoT interface |
|---|---|---|---|---|
| Language / model | Python, asyncio, typed | Python, threads | C#/Java | REST/MQTT |
| License | Apache-2.0 | GPL | proprietary (free) | n/a |
| Streaming inventory + Impinj report content (sub-dBm RSSI, phase, Doppler, TID) | yes | partial | yes | yes |
| TagFocus / search modes | yes | yes | yes | yes |
| Tag select filters (EPC prefix/mask, include & exclude) | yes | limited | yes | yes |
| Tag memory read/write, EPC rewrite, kill | yes | yes | yes | partial |
| GPIO state, outputs, GPI events | yes | limited | yes | yes |
| Reader-mode guidance joined to the live RFModeTable | yes | no | no | no |
| Antenna health monitoring with quiet-port alerts | yes | no | no | no |
| Presence (arrive/depart) events | yes | no | no | yes |
| GS1 EPC decoding (SGTIN/SSCC/... to GTIN + serial) | yes | no | no | no |
| MQTT publishing (tags, presence, availability + Last Will) | yes | no | no | yes |
| Capture to CSV/JSONL | yes | no | via ItemTest | no |
| Power / RF-mode survey tooling | yes | no | no | no |
| Web dashboard | yes | separate GUI project | no | reader web UI |
| Reader emulator (zero-hardware development and CI) | yes | no | no | no |
| Runs without vendor hardware present | yes (emulator) | no | no | no |

*The non-llrpkit columns summarize each project's public documentation as of
mid-2026 — check their current releases when it matters; all of them evolve.*

## When to use something else

Building on .NET/Java: Octane SDK. Pure MQTT integration with no tuning
requirements and no other LLRP clients: the reader's own IoT interface is
simpler — one less process. Reader fleets outside Impinj (Zebra FX as a
primary target): sllurp's tested coverage there is broader today. And for
xArray/xSpan gateway location features, the vendor stack is the only game
in town.
