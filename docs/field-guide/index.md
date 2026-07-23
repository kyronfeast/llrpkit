# The field guide

The half of this project that isn't code. RAIN RFID runs on operational
folklore — knowledge that lives in vendor support portals and integrators'
heads — and the field guide writes it down in plain English, connected
directly to the llrpkit APIs that implement it.

Start with **[LLRP in plain English](llrp-basics.md)** if the ROSpec model is
new to you; it is the mental model everything else builds on. Then
**[Sessions and targets](sessions-and-targets.md)** — the knob that quietly
decides which tags answer and how often, and the first thing to check when a
report stream looks wrong. **[Reader modes](reader-modes.md)** covers the
RF-side counterpart: FM0 versus Miller, dense-reader mode, the AutoSet
families, and a tuning methodology that changes one thing and watches one
number. **[TagFocus and serialized TID](tagfocus-and-tid.md)** explains the
two Impinj extensions that earn their keep in nearly every deployment, and
**[Antenna placement and health](antenna-health.md)** is the monitoring
methodology behind llrpkit's quiet-port alerts. Bringing up a new R700?
**[R700 onboarding](r700-onboarding.md)** has the verified interface-switch
steps that stand between you and your first tag read.

Every chapter is demonstrable without hardware: `llrpkit demo` gives you an
emulated reader whose behavior — TagFocus suppression, mode-dependent read
rates, antenna faults — matches what these pages describe.
