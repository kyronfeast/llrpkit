# TagFocus and serialized TID

Two Impinj Octane extensions do more practical work than everything else in
the vendor catalog combined: **TagFocus**, which turns "this tag again, 400
times a second" into "this tag, once", and **serialized TID**, which gives you
an identifier that survives sloppy EPC management.

## TagFocus

The problem: at a dock door you want each tag reported approximately once per
visit. Plain inventory gives you a firehose of repeats — every tag, every
round, for the seconds or minutes it sits in the field — and you end up
writing debouncing logic downstream.

TagFocus (formally *single target inventory with suppression*, search mode 3)
moves that debouncing into the tags themselves. It runs single-target rounds
in **session 1**, and while a tag stays energized, the reader keeps refreshing
the tag's S1 flag so it *stays* in the quiet state instead of decaying back.
Each tag announces itself when it arrives; the field then goes nearly silent;
a tag only reappears after genuinely leaving the field long enough for its
flag to decay.

```python
async for tag in reader.inventory(session=1, search_mode=3):
    handle_arrival(tag)   # ~one report per tag per visit
```

The requirements are strict and the failure mode is silent: TagFocus needs the
Octane extensions handshake (llrpkit does it automatically on Impinj readers),
**session 1**, and that search mode. Ask for it in S2 and you simply get
normal single-target behavior. llrpkit's emulator models the suppression, so
the difference between search modes 2 and 3 is visible in the demo dashboard:
dual target streams continuously, TagFocus finds the population and goes
quiet.

When *not* to use it: any application that needs continuous presence ("is the
pallet still here?"). A suppressed tag is a silent tag; silence stops meaning
"gone".

## Serialized TID

EPCs are writable, duplicated by careless encoding, and occasionally simply
wrong. The **TID** bank is factory-programmed: a vendor/model header plus, on
serialized parts, a unique serial number. When your application needs an
identity that no label printer can corrupt, ask for TID in reports:

```python
async for tag in reader.inventory(include_tid=True):
    print(tag.epc_hex, tag.tid.hex() if tag.tid else "-")
```

Under the hood this is the `ImpinjSerializedTID` report content (Impinj's
FastID feature makes it cheap on Monza-family tags). Expect a modest read-rate
cost — the tag is sending more bits per report.

## The RF garnish: phase and Doppler

The same report-content machinery carries **RF phase angle** and **Doppler
frequency** per read. One read's phase is meaningless on its own, but phase
*across* reads — over time, channels, and antennas — is the raw material for
velocity estimation, direction-of-travel at portals, and coarse localization.
llrpkit converts units for you (`phase_deg`, `doppler_hz`) and the dashboard's
live table shows phase so you can watch it spin as a tag moves. Treat these as
inputs to statistics, never as single-sample truths.
