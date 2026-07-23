# llrpkit

**A modern, typed, asyncio-native Python toolkit for LLRP RAIN RFID readers — built Impinj-first (R700 and Speedway), with a reader emulator, a web dashboard, and a written field guide.**

> 🚧 **Pre-release.** llrpkit is being built in the open toward `v0.1.0`. Already real: the
> full LLRP 1.0.1 wire protocol with Impinj Octane extensions (golden-vector tested), the
> asyncio `Reader` with streaming inventory, curated reader-mode guidance (`llrpkit modes`),
> antenna health monitoring with quiet-port alerts, settings profiles, and an emulator whose
> behavior *responds to tuning* — switch to Dense Reader M8 and watch the read rate drop,
> enable TagFocus and watch the population go quiet after first reads. No hardware required.
> The web dashboard lands next.

## Why

The RAIN RFID open-source space is thin in a specific way. LLRP — the EPCglobal/GS1 Low Level
Reader Protocol — is stable and everywhere, yet a Python developer who wants to talk to an
Impinj reader today has essentially one option, and it is GPL-licensed, threading-based, and
designed around the previous reader generation. Impinj's official SDKs are .NET and Java only.
There is no permissively licensed, asyncio-native, typed Python library that treats the R700
and its Octane LLRP extensions as a first-class target — and nothing in the space that you can
try without physical hardware on your desk.

llrpkit exists to close all of that at once:

| Piece | What it is | Lands in |
|---|---|---|
| **Library** | Typed asyncio LLRP client: capability-aware config, inventory streams, Impinj Octane extensions (TagFocus, peak RSSI, phase, serialized TID) | Phases 1–3 |
| **Emulator** | A fake Impinj-style reader speaking real LLRP over TCP — the test rig, the CI backbone, and a zero-hardware demo | Phases 2–3 |
| **Dashboard** | FastAPI + WebSockets: multi-reader management, live tag streams, antenna health cards, an interactive reader-mode tuning workbench | Phase 4 |
| **Field guide** | Plain-English docs for the folklore: sessions and targets, reader modes, dense-reader environments, antenna health methodology | Phase 5 |

## Try it in sixty seconds (no reader required)

```console
$ pip install -e .
$ llrpkit emulate --port 5084 &      # a fake Impinj-style reader
$ llrpkit inventory 127.0.0.1 --search-mode tagfocus --phase --count 5
connected: model 700, firmware 'llrpkit-emu 0.1', 4 antenna ports (Octane extensions on)
e2000017010b016210000002  ant 3   -52.06 dBm  phase  340.0°
...
$ llrpkit capabilities 127.0.0.1     # power table, RF modes, antenna ports
```

## The API

```python
import asyncio

from llrpkit import Reader


async def main() -> None:
    async with Reader("192.168.1.10") as reader:
        print(reader.model_number, reader.firmware)
        async for tag in reader.inventory(antennas=(1, 2), session=1, search_mode=3):
            print(tag.epc_hex, tag.antenna, tag.rssi_dbm)


asyncio.run(main())
```

Works identically against the emulator (`Reader("127.0.0.1", emu.port)`), a Speedway,
or an R700 in LLRP mode. The full dashboard demo (`llrpkit demo`) arrives with Phase 4.

## Roadmap

Phase 0 (this scaffold) → 1 wire protocol & codegen → 2 client & inventory → 3 tuning &
antenna health → 4 dashboard → 5 docs, hardening, `v0.1.0` on PyPI. Details in
[`CHANGELOG.md`](CHANGELOG.md) as phases land.

## Development

```console
$ git clone <repo-url> && cd llrpkit
$ pip install -e ".[dev,docs]"
$ pre-commit install
$ pytest          # tests + coverage
$ ruff check . && ruff format --check . && mypy
$ mkdocs serve    # docs at http://127.0.0.1:8000
```

You do not need an RFID reader to contribute — the emulator is the development target.
See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## License

[MIT](LICENSE).

## Trademarks

Impinj, Speedway, R700, Octane, and related marks are trademarks of Impinj, Inc.
This project is an independent open-source effort and is not affiliated with,
sponsored, or endorsed by Impinj.
