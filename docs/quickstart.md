# Quickstart

## Install (from source, pre-release)

llrpkit is not on PyPI yet — that happens at `v0.1.0`. Until then:

```console
$ git clone <repo-url> && cd llrpkit
$ pip install -e .
$ llrpkit --version
llrpkit 0.1.0.dev0
```

## The sixty-second demo *(upcoming — Phase 4)*

The end state this project is built around: a full experience with zero hardware.

```console
$ pip install llrpkit
$ llrpkit demo
```

This will start the built-in reader emulator and open the dashboard against it — live tag
stream, antenna health cards, and the reader-mode tuning workbench, with synthetic tag
populations that respond believably to configuration changes.

## Reading tags from a real reader *(upcoming — Phase 2)*

```console
$ llrpkit inventory --host 192.168.1.10 --antennas 1,2 --session 1
```

!!! note "Using an Impinj R700?"
    The R700 has two control planes: LLRP and the IoT Device Interface (REST). llrpkit
    speaks LLRP, and the field guide will include a step-by-step page on selecting the
    RAIN interface on the R700 before connecting.
