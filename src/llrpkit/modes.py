"""Curated reader-mode knowledge, layered over what the reader itself reports.

The robust way to handle Impinj reader modes — and the one almost nobody
implements — is to parse the ``RFModeTable`` the reader returns in its
capabilities and *then* overlay human knowledge keyed by mode identifier.
llrpkit never assumes a mode exists: :func:`annotate_modes` only decorates
entries the connected reader actually reported, so an R700, a Speedway
R420, and the emulator each get exactly their own truth, annotated.

The curated guidance below is written from Impinj's published Octane LLRP
documentation and field experience; identifiers not in the table still get a
useful generic description derived from their modulation parameters.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from llrpkit.reader import RFMode


@dataclass(frozen=True)
class ModeGuidance:
    """Human knowledge about one RF mode identifier.

    ``speed`` and ``resilience`` are 1-5 relative scores: speed is raw
    singulation throughput in a clean environment; resilience is sensitivity
    plus tolerance of RF interference and reader-dense sites.
    """

    mode_id: int
    name: str
    family: str  # "fixed" | "autoset"
    modulation: str
    speed: int
    resilience: int
    guidance: str


_C = ModeGuidance

#: Curated Impinj mode identifiers. Which of these a given reader supports
#: comes from its RFModeTable; entries here that the reader doesn't report
#: are simply never shown.
CURATED_MODES: dict[int, ModeGuidance] = {
    0: _C(
        0,
        "Max Throughput",
        "fixed",
        "FM0, fastest backscatter",
        5,
        1,
        "Fastest reads, most fragile. Use with one reader, a quiet RF "
        "environment, and tags close to the antenna. First to fall apart in "
        "dense-reader sites.",
    ),
    1: _C(
        1,
        "Hybrid",
        "fixed",
        "Miller-2",
        4,
        2,
        "A middle ground: most of Max Throughput's speed with a little "
        "protection. Reasonable for small single-reader deployments.",
    ),
    2: _C(
        2,
        "Dense Reader M4",
        "fixed",
        "Miller-4",
        3,
        4,
        "The workhorse. Dense-reader-mode compliant, solid sensitivity, good "
        "interference tolerance. When in doubt, start here.",
    ),
    3: _C(
        3,
        "Dense Reader M8",
        "fixed",
        "Miller-8",
        2,
        5,
        "Maximum sensitivity and interference tolerance, at the lowest data "
        "rate. For the hardest environments: many readers, weak or distant "
        "tags, hostile RF.",
    ),
    4: _C(
        4,
        "Max Miller",
        "fixed",
        "Miller-4, high rate",
        4,
        3,
        "Speedway-era fast Miller-4: better throughput than Dense Reader M4 "
        "with some of its tolerance, but not DRM spectrally compliant — avoid "
        "where regulatory dense-reader masks matter.",
    ),
    5: _C(
        5,
        "Dense Reader M4 Two",
        "fixed",
        "Miller-4 variant",
        3,
        4,
        "Speedway R420 alternative M4 profile with different link timing; "
        "try it if standard M4 struggles with your tag population.",
    ),
    1000: _C(
        1000,
        "AutoSet Dense Reader",
        "autoset",
        "reader-managed",
        3,
        4,
        "The reader continuously picks among dense-reader profiles based on "
        "observed interference. A strong default on Speedway readers.",
    ),
    1002: _C(
        1002,
        "AutoSet Dense Reader Deep Scan",
        "autoset",
        "reader-managed",
        2,
        5,
        "AutoSet with extra deep-scan passes for weak or hard-to-read tags; "
        "trades read rate for finding stragglers.",
    ),
    1003: _C(
        1003,
        "AutoSet Static Fast",
        "autoset",
        "reader-managed",
        4,
        3,
        "Auto-selecting with a throughput bias (R700 family). Good when the "
        "environment is mostly clean but you still want the reader to adapt.",
    ),
    1004: _C(
        1004,
        "AutoSet Static DRM",
        "autoset",
        "reader-managed",
        3,
        5,
        "Auto-selecting with a dense-reader bias (R700 family). The safe "
        "choice for multi-reader deployments on modern readers.",
    ),
}


@dataclass(frozen=True)
class AnnotatedMode:
    """A reader-reported RF mode joined with curated guidance (if any)."""

    rf: RFMode
    guidance: ModeGuidance | None

    @property
    def mode_id(self) -> int:
        return self.rf.mode_id

    @property
    def name(self) -> str:
        if self.guidance is not None:
            return self.guidance.name
        return f"Mode {self.rf.mode_id}"

    @property
    def summary(self) -> str:
        if self.guidance is not None:
            return self.guidance.guidance
        return generic_description(self.rf)

    @property
    def is_autoset(self) -> bool:
        if self.guidance is not None:
            return self.guidance.family == "autoset"
        return self.rf.mode_id >= 1000


def generic_description(rf: RFMode) -> str:
    """A serviceable description for a mode we have no curated entry for."""
    miller = "FM0" if rf.m_value == 0 else f"Miller-{2**rf.m_value}"
    kind = "reader-managed (AutoSet-style)" if rf.mode_id >= 1000 else "fixed"
    return (
        f"{kind} mode, {miller} backscatter at {rf.bdr_value} bps. Higher Miller "
        "numbers trade speed for sensitivity and interference tolerance."
    )


def annotate_modes(modes: Iterable[RFMode]) -> list[AnnotatedMode]:
    """Join the reader's RFModeTable with the curated table, reader order kept."""
    return [AnnotatedMode(rf=m, guidance=CURATED_MODES.get(m.mode_id)) for m in modes]


def suggest_mode(
    modes: Iterable[RFMode],
    *,
    dense_environment: bool = False,
    prioritize_speed: bool = False,
) -> tuple[AnnotatedMode, str]:
    """Pick a sensible mode from what this reader offers, with the reasoning.

    Deliberately simple and transparent — this exists so the dashboard's
    tuning workbench can offer a starting point, not to replace judgment.
    """
    annotated = annotate_modes(modes)
    if not annotated:
        raise ValueError("reader reported no RF modes")
    by_id = {m.mode_id: m for m in annotated}

    def first_available(*ids: int) -> AnnotatedMode | None:
        for mode_id in ids:
            if mode_id in by_id:
                return by_id[mode_id]
        return None

    if dense_environment and prioritize_speed:
        pick = first_available(1004, 1000, 2, 4, 1002, 3)
        reason = (
            "dense environment wins over speed: a DRM-compliant profile keeps "
            "readers from jamming each other, which costs more throughput than "
            "any mode setting gains"
        )
    elif dense_environment:
        pick = first_available(1004, 1000, 1002, 3, 2)
        reason = "dense environment: reader-managed DRM (or Miller-8) rides out interference"
    elif prioritize_speed:
        pick = first_available(1003, 0, 1, 4, 2)
        reason = "clean environment and speed prioritized: throughput-biased profile"
    else:
        pick = first_available(1004, 1003, 1000, 2)
        reason = "balanced default: reader-managed profile, or Dense Reader M4"
    if pick is None:
        pick = annotated[0]
        reason = "no curated match in this reader's mode table; using its first entry"
    return pick, reason
