# Sessions and targets, demystified

Nothing in RAIN RFID causes more quiet confusion than session numbers. They
look like an enum you pick arbitrarily; they are actually the knob that decides
**which tags answer, how often** — and the wrong choice makes a system look
broken in ways that resist debugging.

## The inventory flag

Every Gen2 tag keeps, per session, a one-bit **inventoried flag**: A or B.
An inventory round targets one value — "everyone in state A, answer" — and
each tag that is read flips its flag. Read the A-side of a population and,
one by one, tags flip to B and go silent for the rest of the round. What
happens *next* is what the session number controls: how long a tag remembers
being flipped.

| Session | Flag persistence | What that means in practice |
|---|---|---|
| **S0** | Resets the instant the tag loses power (and decays immediately even in-field) | Tags answer over and over, every round. Maximum reads per tag. |
| **S1** | Persists 0.5–5 s, *even while powered* — then decays back to A | Tags go quiet briefly after being read, then reappear. The only session with a built-in timer. |
| **S2 / S3** | Persists indefinitely while the tag is energized, and for at least 2 s after power is lost | Read once, a tag stays quiet until it has been out of the field for a while. |

S2 and S3 are identical in behavior; there are two so that *different readers
can inventory the same tags without interfering* — a portal on S2 and a
handheld on S3 each get their own flag.

## Single target, dual target

The **target** setting says what the round does with the flags. *Single
target* reads A-state tags only, flipping them to B — combined with S2/S3 you
sweep a population once, efficiently, and it stays swept. *Dual target*
alternates A→B and B→A, so every tag answers in every pair of rounds — full
continuous visibility, at the cost of every tag talking all the time.

## Choosing, by scenario

**A handful of tags you want to see continuously** (a demo, a test bench, item
tracking on a desk): S0 or S1, dual target. You want chatter.

**A dock door that must report each pallet's tags once** as they pass: S1 with
**TagFocus** (Impinj's single-target-with-suppression — see the
[TagFocus chapter](tagfocus-and-tid.md)). Each tag announces itself, then
holds quiet while it remains in the field; the S1 timer means nothing is
suppressed forever.

**A large static population** (a stockroom count of thousands): S2, single
target A→B. Every tag answers exactly once per sweep; rounds get faster as the
remaining A-state population shrinks. Re-sweep by inverting the target or
waiting out persistence.

**Multiple readers covering the same tags**: give them different sessions
(S2 vs S3), or the same session *deliberately* if you want them to share one
"already counted" state.

## Q and the population estimate

Within a round, tags pick random slots in a window of size 2^Q. Q too small →
collisions; too large → empty slots and wasted time. Readers adapt Q
automatically, but they start from your **tag population estimate** —
llrpkit's `tag_population` argument. Set it to the right order of magnitude
(30 vs 300 vs 3000); precision beyond that is wasted effort.

## The debugging tell

The classic symptom of a session mistake: "the reader sees the tag once and
never again" (S2/S3 single target where you wanted continuous visibility), or
its mirror, "read rates are huge but I can't tell when a tag *leaves*" (S0
dual target where you wanted once-per-visit semantics). When a report stream
looks wrong, check session and target before touching power or antennas —
llrpkit's emulator reproduces both behaviors if you want to see them side by
side without hardware (`search_mode` 2 vs 3 in the demo).
