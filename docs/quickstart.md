# Quickstart

## Install (from source, pre-release)

llrpkit is not on PyPI yet — that happens at `v0.1.0`. Until then:

```console
$ git clone <repo-url> && cd llrpkit
$ pip install -e .
$ llrpkit --version
llrpkit 0.1.0.dev0
```

## The sixty-second demo — no hardware required

Start the built-in reader emulator in one terminal:

```console
$ llrpkit emulate --port 5084
emulated Impinj-style reader on port 5084 (12 tags, 4 antennas, ~50 reads/s)
```

Stream an inventory from it in another — TagFocus search mode, RF phase enabled:

```console
$ llrpkit inventory 127.0.0.1 --search-mode tagfocus --phase --count 5
connected: model 700, firmware 'llrpkit-emu 0.1', 4 antenna ports (Octane extensions on)
e2000017010b016210000002  ant 3   -52.06 dBm  phase  340.0°
...
5 tag report(s)
```

`llrpkit capabilities 127.0.0.1` shows what the reader reports about itself: antenna
ports, the transmit power table, and the RF mode table. Everything works the same
against real Impinj hardware — point the commands at the reader's address.
(`llrpkit demo`, the emulator plus the web dashboard in one command, arrives with the
dashboard phase.)

## Reading tags from a real reader

```console
$ llrpkit inventory 192.168.1.10 --antennas 1,2 --session 1 --power 25
```

!!! note "Using an Impinj R700?"
    The R700 has two control planes: LLRP and the IoT Device Interface (REST). llrpkit
    speaks LLRP, and the field guide will include a step-by-step page on selecting the
    RAIN interface on the R700 before connecting.
