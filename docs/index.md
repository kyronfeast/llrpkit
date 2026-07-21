# llrpkit

A modern, typed, asyncio-native Python toolkit for LLRP RAIN RFID readers — built
Impinj-first (R700 and Speedway), with a reader emulator, a web dashboard, and a
written field guide.

!!! warning "Pre-release"
    llrpkit is being built in the open toward `v0.1.0`. This documentation site grows
    with the code; pages marked *upcoming* describe functionality that has a designed
    API but hasn't landed yet.

## What llrpkit is

Three things in one package. A **library**: an asyncio LLRP client with strict typing,
capability-aware configuration, streaming inventory, and first-class support for the Impinj
Octane LLRP extensions (TagFocus, sub-dBm peak RSSI, RF phase, serialized TID). An
**emulator**: a fake Impinj-style reader speaking real LLRP over TCP, which powers the test
suite, CI, and a zero-hardware demo. A **dashboard**: a FastAPI + WebSocket web app for
reader management, live tag streams, antenna health, and interactive reader-mode tuning.

## Where to start

Head to the [Quickstart](quickstart.md) for installation, or the
[Field guide](field-guide/index.md) for the RFID knowledge the library encodes.
Want to help build it? See [Contributing](contributing.md) — no reader hardware required.
