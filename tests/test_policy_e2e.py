"""Policy enforcement end-to-end: ignored tags never leave the inventory stream."""

from __future__ import annotations

import contextlib

from llrpkit.emulator import EmulatedTag
from llrpkit.policy import AntennaPolicy, CatalogEntry, ItemCatalog, ReaderPolicy
from llrpkit.reader import Reader
from tests.test_hardening import make_emulator

# Two item families on two lines: pails on antenna 4, pickles on antenna 4 too.
PAILS = [EmulatedTag(epc=bytes([0xE2, 0x00, 0xAA, i] + [0] * 8), antennas=(4,)) for i in range(3)]
PICKLES = [EmulatedTag(epc=bytes([0xE2, 0x00, 0xBB, i] + [0] * 8), antennas=(4,)) for i in range(3)]


def line4_policy() -> ReaderPolicy:
    catalog = ItemCatalog(
        entries=[
            CatalogEntry(match="epc_prefix", value="e200aa", category="pails"),
            CatalogEntry(match="epc_prefix", value="e200bb", category="pickles-fresh"),
        ]
    )
    return ReaderPolicy(
        catalog=catalog,
        antennas={4: AntennaPolicy(mode="allow", categories={"pails"})},
    )


async def _collect(reader: Reader, n: int, **kwargs: object) -> list:  # type: ignore[type-arg]
    seen = []
    stream = reader.inventory(max_tags=n, duration=8.0, **kwargs)  # type: ignore[arg-type]
    async with contextlib.aclosing(stream):
        async for t in stream:
            seen.append(t)
    return seen


async def test_line4_stream_yields_only_pails() -> None:
    policy = line4_policy()
    async with (
        make_emulator(tags=PAILS + PICKLES, reads_per_sec=400.0) as emu,
        Reader("127.0.0.1", emu.port) as reader,
    ):
        tags = await _collect(reader, 20, policy=policy)
    assert tags, "policy stream produced nothing"
    # every surviving tag is a pail, tagged with its category
    assert all(t.epc[:3] == bytes([0xE2, 0x00, 0xAA]) for t in tags)
    assert all(t.category == "pails" for t in tags)
    # and pickles were counted as drops, not silently lost
    snap = policy.counters()
    assert snap["dropped"] > 0
    assert snap["by_category"].get("pickles-fresh", 0) > 0
    assert snap["by_antenna"].get("4", 0) > 0


async def test_no_policy_yields_everything() -> None:
    async with (
        make_emulator(tags=PAILS + PICKLES, reads_per_sec=400.0) as emu,
        Reader("127.0.0.1", emu.port) as reader,
    ):
        tags = await _collect(reader, 30)
    families = {t.epc[:3] for t in tags}
    assert bytes([0xE2, 0x00, 0xAA]) in families
    assert bytes([0xE2, 0x00, 0xBB]) in families
    assert all(t.category is None for t in tags)  # unclassified without a policy


async def test_policy_applies_everywhere_via_mqtt_bridge() -> None:
    """The bridge consumes reader.inventory(policy=...), so ignored tags never
    reach the broker — the 'everywhere' guarantee, one layer up."""
    import pytest

    pytest.importorskip("aiomqtt")
    from llrpkit.mqtt import tag_payload

    policy = line4_policy()
    async with (
        make_emulator(tags=PAILS + PICKLES, reads_per_sec=400.0) as emu,
        Reader("127.0.0.1", emu.port) as reader,
    ):
        # Simulate what the bridge does: iterate the policy-filtered stream and
        # build payloads. Only pails should ever be turned into a payload.
        payloads = []
        stream = reader.inventory(max_tags=15, duration=8.0, policy=policy)
        async with contextlib.aclosing(stream):
            async for t in stream:
                payloads.append(tag_payload(t, "line-4"))
    assert payloads
    assert all(p["epc"].startswith("e200aa") for p in payloads)
