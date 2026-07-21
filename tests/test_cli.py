"""CLI smoke tests."""

from __future__ import annotations

from typer.testing import CliRunner

from llrpkit import __version__
from llrpkit.cli import app

runner = CliRunner()


def test_version_flag() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_version_command() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_help_mentions_impinj() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Impinj" in result.output
