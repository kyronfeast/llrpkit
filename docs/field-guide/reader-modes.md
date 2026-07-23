# Reader modes

The RF mode decides how reader and tags modulate their conversation — and with
it, how fast you read and how well you survive interference. It is the single
highest-leverage tuning knob on an Impinj reader, and the most cargo-culted.

## The physics, in three sentences

The reader talks downlink with simple amplitude modulation; tags answer by
*backscatter* — flipping their antenna impedance to reflect the reader's own
carrier. That reflected whisper can be encoded as **FM0** (one symbol per bit,
fast, fragile) or **Miller** subcarrier codings (M=2/4/8: each bit smeared over
more transitions, slower, but far easier to pull out of noise and further from
the reader's own transmit spectrum). Higher M is how a tag 10 meters away in
an aisle full of readers still gets heard.

## Dense-reader mode

When many readers operate near each other, the limiting factor is readers
jamming each other's receive windows. **DRM** (dense reader mode) profiles put
the reader's transmit energy inside strict spectral masks and use Miller
codings whose subcarrier moves tag replies *between* those masks. The through-
put you "lose" choosing a DRM profile is usually a fraction of what you lose
letting readers fight. That is why the guidance below keeps saying the same
thing: dense site → DRM.

## Read the table, don't assume it

Readers report their supported modes in the capabilities `RFModeTable`, keyed
by *mode identifier*. The identifiers are stable per vendor family, but the
**set varies by model, region, and firmware** — hardcoding "mode 2" works
until the day it doesn't. llrpkit therefore treats the reader's table as truth
and layers curated knowledge on top (`llrpkit modes`, `Reader.annotated_modes()`,
`llrpkit.modes.suggest_mode()`).

The curated identifiers, abbreviated:

| id | name | character |
|---|---|---|
| 0 | Max Throughput | FM0-fast, fragile; one reader, clean RF, close tags |
| 1 | Hybrid | Miller-2 middle ground |
| 2 | Dense Reader M4 | the workhorse; when in doubt, start here |
| 3 | Dense Reader M8 | slowest, most sensitive; hardest environments |
| 4 | Max Miller | fast M4 (Speedway); not DRM-compliant |
| 1000 | AutoSet Dense Reader | Speedway: reader picks among DRM profiles |
| 1002 | AutoSet DR Deep Scan | adds deep-scan passes for weak tags |
| 1003 | AutoSet Static Fast | R700 family: adaptive, throughput bias |
| 1004 | AutoSet Static DRM | R700 family: adaptive, dense-reader bias |

**AutoSet** families deserve a special word: the reader continuously measures
its own RF environment and hops among underlying profiles. On modern readers
they are the right default — you choose the *bias* (fast vs DRM) and let the
firmware do the per-minute adaptation you were never going to do by hand.

## How to actually tune

Change one thing, watch one number. The methodology llrpkit's tuning workbench
is built around:

1. Fix your population and layout; pick session/target first (they change
   *what* answers — see [sessions](sessions-and-targets.md)).
2. Sweep candidate modes for ~30 s each and record reads/sec **and** unique
   count — a mode that reads fewer tags per second but finds stragglers the
   fast mode misses is often the winner.
3. Only then touch power, and prefer *less*: the goal is reading your tags,
   not neighboring zones' tags.

`examples/mode_shootout.py` automates step 2, and the emulator responds to
mode changes realistically enough to practice the workflow with zero hardware
(`llrpkit demo`, Tuning tab).
