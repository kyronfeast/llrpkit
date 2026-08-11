"""CLI argument validation: clear errors before any reader is contacted."""

from __future__ import annotations

import pytest
import typer
from typer.testing import CliRunner

from llrpkit.cli import _parse_antennas, _parse_broker, app

runner = CliRunner()


def test_parse_broker_forms() -> None:
    assert _parse_broker("broker.local") == ("broker.local", 1883)
    assert _parse_broker("10.0.0.5:8883") == ("10.0.0.5", 8883)
    with pytest.raises(typer.BadParameter, match="no hostname"):
        _parse_broker(":1883")
    with pytest.raises(typer.BadParameter, match="not an integer"):
        _parse_broker("host:llrp")


def test_parse_antennas_forms() -> None:
    assert _parse_antennas("0") == []
    assert _parse_antennas("1,3") == [1, 3]
    with pytest.raises(typer.BadParameter):
        _parse_antennas("1,two")


def test_inventory_rejects_bad_search_mode() -> None:
    result = runner.invoke(app, ["inventory", "h", "--search-mode", "warp"])
    assert result.exit_code != 0
    assert "search mode must be one of" in result.output


def test_inventory_rejects_bad_filter_arguments() -> None:
    result = runner.invoke(app, ["inventory", "h", "--filter-epc", "zz"])
    assert result.exit_code != 0
    assert "not valid hex" in result.output
    result = runner.invoke(
        app, ["inventory", "h", "--filter-epc", "e2", "--filter-action", "sideways"]
    )
    assert result.exit_code != 0
    assert 'must be "include" or "exclude"' in result.output


def test_gpio_rejects_bad_set_arguments() -> None:
    result = runner.invoke(app, ["gpio", "h", "--set", "one=on"])
    assert result.exit_code != 0
    assert "port is not an integer" in result.output
    result = runner.invoke(app, ["gpio", "h", "--set", "1=maybe"])
    assert result.exit_code != 0
    assert "value must be on/off" in result.output


def test_access_commands_reject_bad_hex() -> None:
    result = runner.invoke(app, ["write", "h", "--data", "xyz"])
    assert result.exit_code != 0
    assert "not valid hex" in result.output
    result = runner.invoke(app, ["read", "h", "--epc", "gg"])
    assert result.exit_code != 0
    assert "not valid hex" in result.output
    result = runner.invoke(app, ["write-epc", "h", "--new-epc", "nope"])
    assert result.exit_code != 0
    assert "not valid hex" in result.output


def test_sweep_rejects_unparseable_values() -> None:
    result = runner.invoke(app, ["sweep", "h", "--powers", "15,loud"])
    assert result.exit_code != 0
    assert "could not parse sweep values" in result.output


def test_profile_show_and_delete_missing(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import llrpkit.cli as cli

    monkeypatch.setattr(cli, "PROFILE_DIR", tmp_path)
    assert runner.invoke(app, ["profile", "show", "nope"]).exit_code == 1
    assert runner.invoke(app, ["profile", "delete", "nope"]).exit_code == 1
    listing = runner.invoke(app, ["profile", "list"])
    assert listing.exit_code == 0
    assert "no profiles saved yet" in listing.output


def test_profile_save_rejects_bad_search_mode(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import llrpkit.cli as cli

    monkeypatch.setattr(cli, "PROFILE_DIR", tmp_path)
    result = runner.invoke(app, ["profile", "save", "x", "--search-mode", "warp"])
    assert result.exit_code != 0
    assert "search mode must be one of" in result.output


def test_epc_company_overflow_returns_none() -> None:
    """A partition-0 company field larger than 12 digits is invalid."""
    from llrpkit.epc import decode_epc

    # header sgtin-96, filter 0, partition 0, company = 2**40-1 (13 digits)
    value = (0x30 << 88) | (0 << 85) | (0 << 82) | ((2**40 - 1) << 42)
    assert decode_epc(value.to_bytes(12, "big")) is None
