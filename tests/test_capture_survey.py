"""Capture files, RF sweeps, and the profiles CLI."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from llrpkit.capture import COLUMNS, TagWriter, tag_row
from llrpkit.emulator import EmulatedTag
from llrpkit.inventory import TagReport
from llrpkit.reader import Reader
from llrpkit.survey import sweep
from tests.test_cli_e2e import EmulatorThread
from tests.test_hardening import make_emulator

runner = CliRunner()


# --- capture ----------------------------------------------------------------


def test_tag_row_includes_gs1_decode() -> None:
    gs1_tag = TagReport(epc=bytes.fromhex("3074257bf7194e4000001a85"), antenna=2)
    row = tag_row(gs1_tag)
    assert row["scheme"] == "sgtin-96"
    assert row["identity"] == "urn:epc:id:sgtin:0614141.812345.6789"
    raw = tag_row(TagReport(epc=b"\xe2" + b"\x00" * 11))
    assert raw["scheme"] is None


def test_tag_writer_csv_and_jsonl(tmp_path: Path) -> None:
    tag = TagReport(epc=b"\xe2\x33" + b"\x00" * 10, antenna=1, rssi_dbm=-51.5)
    csv_path = tmp_path / "reads.csv"
    with TagWriter(csv_path) as writer:
        writer.write(tag)
        writer.write(tag)
    assert writer.rows == 2
    with csv_path.open() as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2
    assert list(rows[0]) == COLUMNS
    assert rows[0]["epc"] == tag.epc_hex
    assert rows[0]["rssi_dbm"] == "-51.5"

    jsonl_path = tmp_path / "reads.jsonl"
    with TagWriter(jsonl_path) as writer:
        writer.write(tag)
    lines = jsonl_path.read_text().strip().splitlines()
    assert json.loads(lines[0])["epc"] == tag.epc_hex

    with pytest.raises(ValueError, match=r"use \.csv or \.jsonl"):
        TagWriter(tmp_path / "reads.xml")


def test_cli_inventory_captures_to_file(tmp_path: Path) -> None:
    from llrpkit.cli import app

    out = tmp_path / "capture.csv"
    with EmulatorThread() as emu:
        result = runner.invoke(
            app,
            [
                "inventory",
                "127.0.0.1",
                "--port",
                str(emu.port),
                "--count",
                "8",
                "--duration",
                "10",
                "--output",
                str(out),
            ],
        )
    assert result.exit_code == 0, result.output
    assert f"8 tag report(s) captured to {out}" in result.output
    with out.open() as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 8
    assert all(row["epc"].startswith("e2") for row in rows)


# --- sweep ------------------------------------------------------------------


async def test_sweep_shows_power_coverage_difference() -> None:
    strong = EmulatedTag(epc=b"\xaa" * 12, antennas=(1,), rssi_dbm=-40.0)
    weak = EmulatedTag(epc=b"\xbb" * 12, antennas=(1,), rssi_dbm=-68.0)
    async with (
        make_emulator(tags=[strong, weak], reads_per_sec=250.0) as emu,
        Reader("127.0.0.1", emu.port) as reader,
    ):
        points = await sweep(reader, powers_dbm=[12.0, 30.0], seconds=1.2, session=1)
    assert len(points) == 2
    low, high = points
    assert low.tx_power_dbm == 12.0
    assert low.unique == 1, "only the strong tag should be energized at 12 dBm"
    assert high.unique == 2, "30 dBm must find both tags"
    assert high.reads_per_sec > 0


def test_cli_sweep_prints_table_and_best() -> None:
    from llrpkit.cli import app

    with EmulatorThread() as emu:
        result = runner.invoke(
            app,
            [
                "sweep",
                "127.0.0.1",
                "--port",
                str(emu.port),
                "--powers",
                "12,30",
                "--seconds",
                "1.0",
            ],
        )
    assert result.exit_code == 0, result.output
    assert "reads/s" in result.output
    assert "best coverage:" in result.output
    assert "30.0" in result.output


def test_cli_sweep_requires_an_axis() -> None:
    from llrpkit.cli import app

    result = runner.invoke(app, ["sweep", "127.0.0.1"])
    assert result.exit_code != 0
    assert "--powers and/or --modes" in result.output


# --- profiles CLI ------------------------------------------------------------


def test_profile_save_list_show_use_delete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import llrpkit.cli as cli

    monkeypatch.setattr(cli, "PROFILE_DIR", tmp_path)
    saved = runner.invoke(
        cli.app,
        [
            "profile",
            "save",
            "dock-1",
            "--search-mode",
            "tagfocus",
            "--session",
            "1",
            "--power",
            "27.5",
            "--antennas",
            "1,2",
            "--tid",
            "--description",
            "east dock door",
        ],
    )
    assert saved.exit_code == 0, saved.output
    listing = runner.invoke(cli.app, ["profile", "list"])
    assert "dock-1" in listing.output
    assert "27.5 dBm" in listing.output
    shown = runner.invoke(cli.app, ["profile", "show", "dock-1"])
    assert shown.exit_code == 0
    body = json.loads(shown.output)
    assert body["search_mode"] == 3
    assert body["antennas"] == [1, 2]

    with EmulatorThread() as emu:
        used = runner.invoke(
            cli.app,
            [
                "inventory",
                "127.0.0.1",
                "--port",
                str(emu.port),
                "--profile",
                "dock-1",
                "--count",
                "3",
                "--duration",
                "10",
            ],
        )
    assert used.exit_code == 0, used.output
    assert "profile 'dock-1'" in used.output
    assert "3 tag report(s)" in used.output

    deleted = runner.invoke(cli.app, ["profile", "delete", "dock-1"])
    assert deleted.exit_code == 0
    assert runner.invoke(cli.app, ["profile", "show", "dock-1"]).exit_code == 1


def test_profile_missing_is_a_clear_error() -> None:
    from llrpkit.cli import app

    result = runner.invoke(app, ["inventory", "127.0.0.1", "--profile", "ghost", "--count", "1"])
    assert result.exit_code != 0
    assert "no profile 'ghost'" in result.output
