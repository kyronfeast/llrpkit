"""The `llrpkit inventory --policy FILE` path, end to end through the CLI."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from llrpkit.cli import app
from tests.test_cli_e2e import EmulatorThread

runner = CliRunner()

# Default emulator EPCs all share this prefix.
POP_PREFIX = "e2000017010b0162"


def _write(path: Path, policy: dict) -> str:  # type: ignore[type-arg]
    path.write_text(json.dumps(policy))
    return str(path)


def test_policy_allow_keeps_and_labels(tmp_path: Path) -> None:
    """A catalog + all-antenna allow keeps every tag and stamps its category."""
    policy_file = _write(
        tmp_path / "keep.json",
        {
            "catalog": [{"match": "epc_prefix", "value": POP_PREFIX, "category": "boxes"}],
            "antennas": {str(a): {"mode": "allow", "categories": ["boxes"]} for a in (1, 2, 3, 4)},
        },
    )
    with EmulatorThread() as emu:
        result = runner.invoke(
            app,
            [
                "inventory",
                "127.0.0.1",
                "--port",
                str(emu.port),
                "--policy",
                policy_file,
                "--count",
                "5",
                "--duration",
                "10",
            ],
        )
    assert result.exit_code == 0, result.output
    assert "(policy:" in result.output
    assert "[boxes]" in result.output  # category shown on the tag rows
    assert "5 tag report(s)" in result.output
    assert "policy ignored" not in result.output  # nothing dropped


def test_policy_ignore_unknown_drops_everything(tmp_path: Path) -> None:
    """An empty catalog with ignore_unknown ignores every tag host-side."""
    policy_file = _write(
        tmp_path / "drop.json",
        {"catalog": [], "ignore_unknown": True},
    )
    with EmulatorThread() as emu:
        result = runner.invoke(
            app,
            [
                "inventory",
                "127.0.0.1",
                "--port",
                str(emu.port),
                "--policy",
                policy_file,
                "--duration",
                "1.5",
            ],
        )
    assert result.exit_code == 0, result.output
    assert "0 tag report(s)" in result.output
    assert "policy ignored" in result.output
    assert "unknown" in result.output


def test_missing_policy_file_is_a_clear_error() -> None:
    result = runner.invoke(app, ["inventory", "h", "--policy", "/no/such/policy.json"])
    assert result.exit_code != 0
    assert "no policy file" in result.output
