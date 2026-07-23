"""Unit tests for the curated mode metadata layer."""

from __future__ import annotations

import pytest

from llrpkit.modes import CURATED_MODES, annotate_modes, generic_description, suggest_mode
from llrpkit.reader import RFMode


def rf(mode_id: int, m: int = 2, bdr: int = 274000) -> RFMode:
    return RFMode(
        mode_id=mode_id,
        m_value=m,
        bdr_value=bdr,
        dr_value=1,
        pie_value=1500,
        min_tari=6250,
        max_tari=25000,
        step_tari=1875,
        forward_link_modulation=0,
        spectral_mask_indicator=2,
        epc_hag_conformance=False,
    )


def test_annotate_joins_curated_knowledge_only_for_reported_modes() -> None:
    reported = [rf(0, m=0, bdr=640000), rf(3, m=3, bdr=170600), rf(424242)]
    annotated = annotate_modes(reported)
    assert [a.mode_id for a in annotated] == [0, 3, 424242]
    assert annotated[0].name == "Max Throughput"
    assert annotated[1].name == "Dense Reader M8"
    assert annotated[1].guidance is not None
    assert "sensitivity" in annotated[1].summary.lower()
    # unknown mode falls back to a generic but informative description
    unknown = annotated[2]
    assert unknown.guidance is None
    assert unknown.name == "Mode 424242"
    assert "Miller-4" in unknown.summary


def test_generic_description_fm0_and_autoset() -> None:
    assert "FM0" in generic_description(rf(7, m=0))
    assert "AutoSet-style" in generic_description(rf(1010))


def test_autoset_detection() -> None:
    annotated = annotate_modes([rf(1003), rf(2), rf(1010)])
    assert annotated[0].is_autoset  # curated autoset
    assert not annotated[1].is_autoset
    assert annotated[2].is_autoset  # uncurated >= 1000 heuristic


def test_curated_table_is_consistent() -> None:
    for mode_id, guidance in CURATED_MODES.items():
        assert guidance.mode_id == mode_id
        assert guidance.family in ("fixed", "autoset")
        assert 1 <= guidance.speed <= 5
        assert 1 <= guidance.resilience <= 5
        assert len(guidance.guidance) > 40  # real prose, not a stub


def test_suggest_prefers_drm_for_dense_environments() -> None:
    modes = [rf(0), rf(2), rf(3), rf(1003)]
    pick, reason = suggest_mode(modes, dense_environment=True)
    assert pick.mode_id == 3  # no AutoSet DRM available -> Miller-8
    assert "dense" in reason
    pick, reason = suggest_mode(modes, prioritize_speed=True)
    assert pick.mode_id == 1003
    pick, reason = suggest_mode(modes, dense_environment=True, prioritize_speed=True)
    assert pick.mode_id == 2  # dense wins over speed
    assert "dense" in reason


def test_suggest_falls_back_to_first_reported_mode() -> None:
    pick, reason = suggest_mode([rf(777)])
    assert pick.mode_id == 777
    assert "first entry" in reason


def test_suggest_requires_modes() -> None:
    with pytest.raises(ValueError, match="no RF modes"):
        suggest_mode([])
