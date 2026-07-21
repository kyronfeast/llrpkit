# The field guide

The half of this project that isn't code. RAIN RFID has a lot of operational folklore —
knowledge that lives in vendor support articles and integrators' heads — and the field guide
writes it down in plain English, connected directly to the llrpkit APIs that implement it.

!!! info "In progress"
    Chapters land through Phases 2–5, roughly in the order below, as the features they
    explain arrive in the library.

Planned chapters: **LLRP in plain English** (the ROSpec model — how LLRP thinks about
inventory); **Sessions and targets, demystified** (S0–S3, A/B flags, and choosing for your tag
population); **Reader modes** (what the RF mode table actually is, AutoSet families, and when
dense-reader mode matters); **TagFocus and serialized TID** (the Impinj extensions that change
what's possible, and their prerequisites); **Antenna placement and health** (why ports go
quiet, and monitoring that catches it); **R700 versus Speedway** (capability differences and
the R700's two control planes).
