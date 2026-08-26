"""Reader policy engine: catalog classification and per-antenna ignore rules."""

from __future__ import annotations

import json

import pytest

from llrpkit.inventory import TagReport
from llrpkit.policy import (
    UNKNOWN_CATEGORY,
    AntennaPolicy,
    CatalogEntry,
    ItemCatalog,
    ReaderPolicy,
)

# A canonical GS1 SGTIN-96 whose decode yields GTIN 80614141123458 / company 0614141.
SGTIN = bytes.fromhex("3074257bf7194e4000001a85")
PAIL = bytes([0xE2, 0x00, 0xAA] + [0] * 9)
PICKLE = bytes([0xE2, 0x00, 0xBB] + [0] * 9)
INGREDIENT = bytes([0xE2, 0x00, 0xCC] + [0] * 9)


def tag(epc: bytes, antenna: int = 1, rssi: float = -50.0) -> TagReport:
    return TagReport(epc=epc, antenna=antenna, rssi_dbm=rssi)


# --- catalog classification --------------------------------------------------


def test_catalog_matches_by_prefix_gtin_and_exact() -> None:
    catalog = ItemCatalog(
        entries=[
            CatalogEntry(match="epc_prefix", value="e200aa", category="pails"),
            CatalogEntry(match="epc_prefix", value="e200bb", category="pickles-fresh"),
            CatalogEntry(
                match="gtin", value="80614141123458", category="ingredients", label="Salt"
            ),
            CatalogEntry(match="epc", value=PICKLE.hex(), category="pickles-special"),
        ]
    )
    assert catalog.classify(tag(PAIL)) == ("pails", "")
    # exact EPC beats the e200bb prefix rule (more specific wins)
    assert catalog.classify(tag(PICKLE)) == ("pickles-special", "")
    assert catalog.classify(tag(SGTIN)) == ("ingredients", "Salt")
    assert catalog.classify(tag(INGREDIENT)) == (UNKNOWN_CATEGORY, "")
    assert catalog.categories == {"pails", "pickles-fresh", "ingredients", "pickles-special"}


def test_longest_prefix_wins() -> None:
    catalog = ItemCatalog(
        entries=[
            CatalogEntry(match="epc_prefix", value="e2", category="broad"),
            CatalogEntry(match="epc_prefix", value="e200aa", category="pails"),
        ]
    )
    assert catalog.classify(tag(PAIL))[0] == "pails"
    assert catalog.classify(tag(PICKLE))[0] == "broad"


def test_catalog_entry_validation() -> None:
    with pytest.raises(ValueError, match="match must be"):
        CatalogEntry(match="color", value="x", category="c")
    with pytest.raises(ValueError, match="value"):
        CatalogEntry(match="epc", value="", category="c")


# --- the core scenario: line 4 sees only pails -------------------------------


def _line4_policy() -> ReaderPolicy:
    catalog = ItemCatalog(
        entries=[
            CatalogEntry(match="epc_prefix", value="e200aa", category="pails"),
            CatalogEntry(match="epc_prefix", value="e200bb", category="pickles-fresh"),
            CatalogEntry(match="epc_prefix", value="e200cc", category="ingredients"),
        ]
    )
    return ReaderPolicy(
        catalog=catalog,
        antennas={4: AntennaPolicy(mode="allow", categories={"pails"})},
    )


def test_line4_allows_pails_ignores_the_rest() -> None:
    policy = _line4_policy()
    assert policy.evaluate(tag(PAIL, antenna=4)).keep is True
    d_pickle = policy.evaluate(tag(PICKLE, antenna=4))
    assert d_pickle.keep is False
    assert d_pickle.category == "pickles-fresh"
    assert d_pickle.reason == "category-not-allowed"
    assert policy.evaluate(tag(INGREDIENT, antenna=4)).keep is False


def test_unconfigured_antenna_passes_everything() -> None:
    policy = _line4_policy()  # only antenna 4 is configured
    assert policy.evaluate(tag(PICKLE, antenna=1)).keep is True
    assert policy.evaluate(tag(INGREDIENT, antenna=2)).keep is True


def test_deny_mode_blocks_only_listed_categories() -> None:
    policy = ReaderPolicy(
        catalog=_line4_policy().catalog,
        antennas={2: AntennaPolicy(mode="deny", categories={"pickles-fresh"})},
    )
    assert policy.evaluate(tag(PAIL, antenna=2)).keep is True
    blocked = policy.evaluate(tag(PICKLE, antenna=2))
    assert blocked.keep is False
    assert blocked.reason == "category-denied"


# --- RSSI floors and unknowns ------------------------------------------------


def test_global_and_per_antenna_rssi_floor() -> None:
    policy = ReaderPolicy(
        catalog=_line4_policy().catalog,
        min_rssi_dbm=-60.0,
        antennas={1: AntennaPolicy(mode="deny", categories=set(), min_rssi_dbm=-45.0)},
    )
    # global floor: -70 dBm pail on an unconfigured antenna is too faint
    assert policy.evaluate(tag(PAIL, antenna=3, rssi=-70.0)).reason == "weak-rssi"
    assert policy.evaluate(tag(PAIL, antenna=3, rssi=-40.0)).keep is True
    # per-antenna stricter floor on antenna 1
    assert policy.evaluate(tag(PAIL, antenna=1, rssi=-50.0)).reason == "weak-rssi"
    assert policy.evaluate(tag(PAIL, antenna=1, rssi=-40.0)).keep is True


def test_ignore_unknown_flag() -> None:
    policy = ReaderPolicy(catalog=_line4_policy().catalog, ignore_unknown=True)
    unknown = tag(bytes([0xF0] + [0] * 11), antenna=1)
    d = policy.evaluate(unknown)
    assert d.keep is False
    assert d.reason == "unknown-ignored"
    # a cataloged tag on the same antenna still passes
    assert policy.evaluate(tag(PAIL, antenna=1)).keep is True


def test_disabled_policy_keeps_everything_but_still_classifies() -> None:
    policy = _line4_policy()
    policy.enabled = False
    d = policy.evaluate(tag(PICKLE, antenna=4))
    assert d.keep is True
    assert d.category == "pickles-fresh"


# --- counters ----------------------------------------------------------------


def test_counters_tally_drops_by_antenna_category_reason() -> None:
    policy = _line4_policy()
    for _ in range(3):
        policy.evaluate(tag(PICKLE, antenna=4))
    policy.evaluate(tag(INGREDIENT, antenna=4))
    policy.evaluate(tag(PAIL, antenna=4))  # kept
    snap = policy.counters()
    assert snap["kept"] == 1
    assert snap["dropped"] == 4
    assert snap["by_antenna"]["4"] == 4
    assert snap["by_category"]["pickles-fresh"] == 3
    assert snap["by_category"]["ingredients"] == 1
    assert snap["by_reason"]["category-not-allowed"] == 4
    policy.reset_counters()
    assert policy.counters()["dropped"] == 0


# --- serialization -----------------------------------------------------------


def test_policy_json_roundtrip(tmp_path) -> None:  # type: ignore[no-untyped-def]
    policy = ReaderPolicy(
        catalog=_line4_policy().catalog,
        antennas={
            4: AntennaPolicy(mode="allow", categories={"pails"}),
            2: AntennaPolicy(mode="deny", categories={"pickles-fresh"}, min_rssi_dbm=-55.0),
        },
        min_rssi_dbm=-65.0,
        ignore_unknown=True,
    )
    path = policy.save(tmp_path / "policy.json")
    reloaded = ReaderPolicy.load(path)
    assert reloaded.to_dict() == policy.to_dict()
    # behavior survives the round trip
    assert reloaded.evaluate(tag(PICKLE, antenna=4)).keep is False
    assert reloaded.evaluate(tag(PAIL, antenna=4)).keep is True
    # and it is valid JSON with the documented shape
    body = json.loads(path.read_text())
    assert body["antennas"]["4"]["mode"] == "allow"
    assert body["antennas"]["4"]["categories"] == ["pails"]


def test_catalog_from_rows_skips_uncategorized() -> None:
    rows = [
        {"match": "gtin", "value": "80614141123458", "category": "ingredients"},
        {"match": "epc_prefix", "value": "e200aa", "category": ""},  # skipped
        {"match": "epc_prefix", "value": "e200bb", "category": "pickles-fresh", "label": "Dill"},
    ]
    catalog = ItemCatalog.from_rows(rows)
    assert len(catalog.entries) == 2
    assert catalog.classify(tag(PICKLE)) == ("pickles-fresh", "Dill")


def test_company_prefix_match_and_decision_ignored() -> None:
    catalog = ItemCatalog(
        entries=[CatalogEntry(match="company_prefix", value="0614141", category="acme")]
    )
    # SGTIN decodes to company prefix 0614141
    assert catalog.classify(tag(SGTIN)) == ("acme", "")
    policy = ReaderPolicy(
        catalog=catalog, antennas={1: AntennaPolicy(mode="allow", categories={"nope"})}
    )
    d = policy.evaluate(tag(SGTIN, antenna=1))
    assert d.ignored is True
    assert d.keep is False


def test_antenna_policy_validation_and_empty_catalog() -> None:
    with pytest.raises(ValueError, match="mode must be"):
        AntennaPolicy(mode="perhaps")
    empty = ItemCatalog()
    assert empty.classify(tag(PAIL)) == (UNKNOWN_CATEGORY, "")
    assert empty.categories == set()
