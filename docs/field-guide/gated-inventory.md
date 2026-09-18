# Gated inventory: one window per object

Most production lines don't want a firehose of tag reads. They want to know
*"a pail just went past station 3, and these are the tags it carried."* And,
just as much, *"a pail went past and we read nothing"* — because that's the one
that ends up in the wrong brine.

**Gated inventory** does exactly that. A photo eye (or any switch) wired to one
of the reader's GPI lines tells the reader when something is in front of the
antenna. The reader itself starts reading when the line trips and stops when
it releases — no round-trip to your software per object — and llrpkit folds
what it read into one `InventoryWindow` per trip.

## In three lines

```python
async with Reader("10.0.0.1") as reader:
    async for window in reader.windows(gpi_trigger=1):
        print(window.opened_at, window.epcs or "NOTHING READ")
```

Each `InventoryWindow` has `opened_at` / `closed_at` (Unix seconds), `tags`
(every observation, repeats included), `epcs` (the distinct EPCs, first-seen
order), `duration`, and `empty`. An **empty window is still delivered** — it is
the exception the line exists to catch.

## How it works on the reader

LLRP lets a ROSpec carry a *GPI start trigger* and a *GPI-with-timeout stop
trigger*. `Reader.inventory(gpi_trigger=N)` builds exactly that and only
`ENABLE`s it — the reader arms it, starts it on the next edge, stops it on the
opposite edge, and **re-arms it** for the following object. The reader also
sends a `GPIEvent` on every edge; llrpkit surfaces those as
`Reader.gpi_events()`, a feed of `GPIEdge(port, high, at)` kept separate from
the health-event feed so the two never compete.

`Reader.windows()` runs both feeds together and folds them:

| moment | what happens |
|---|---|
| line goes **active** | a window opens (`opened_at` = the edge's timestamp) |
| tags arrive | collected into the open window; tags with no window open are dropped as noise |
| line goes **inactive** | `closed_at` recorded; a short **settle** grace (default 100 ms) catches reports still in flight — LLRP sends the edge and the last reports on different channels, in no guaranteed order |
| settle elapses | the window is yielded |
| no release ever comes | `max_open` (default 30 s) force-closes it |

`gpi_stop_timeout=` additionally tells the *reader* to stop on its own after N
seconds if the release never arrives — belt and braces for a stuck sensor.

## Wiring: which level is "active"?

The R700's GPI inputs are **optically isolated** and read *low* with nothing
connected. A sensor — or the relay it drives — that puts voltage on the pin
makes it *high*. So on an R700 **active-high is the natural wiring** and the
default (`gpi_active_high=True`). If your sensor sinks the line instead, pass
`gpi_active_high=False`.

Practical notes from the field:

- Put an **interposing relay** between an industrial 24 V photo eye and the
  reader rather than wiring it direct. It isolates the reader, works the same
  on every reader you own, and lets you add a small **off-delay** (200–500 ms)
  so the window stays open while the tag clears the antenna instead of slamming
  shut the instant the beam clears.
- Mount the eye so the beam is broken *while the tag is in the antenna's field*,
  not before it arrives.
- Use `emu.set_gpi(port, True/False)` on `LLRPEmulator` to play the photo eye
  in tests — the emulator honours GPI start/stop triggers and their timeout
  exactly as above.

## Why not poll the GPI from software?

You can (`Reader.get_gpio()`), and for a bench it's fine. On a line it means a
host round-trip decides when reading starts, so a busy host or a hiccup on the
network delays the read window — and the pail doesn't wait. Letting the reader
own the trigger removes the host from the timing path entirely; llrpkit only
has to *label* what it already read.

See also the [API tour](../api.md) for `GPIEdge`, `InventoryWindow`, and
`llrpkit.gating.assemble_windows` (the reader-agnostic folding step, reused by
[OmniTag](https://kyronfeast.github.io/omnitag/) for serial readers).
