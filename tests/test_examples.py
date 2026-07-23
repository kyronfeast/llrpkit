"""Every example script must actually run, end to end, against the emulator."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_cli_e2e import EmulatorThread

EXAMPLES = sorted((Path(__file__).parents[1] / "examples").glob("*.py"))


@pytest.fixture(name="emu_port", scope="module")
def fixture_emu_port():  # type: ignore[no-untyped-def]
    with EmulatorThread() as emu:
        yield emu.port


@pytest.mark.parametrize("script", EXAMPLES, ids=lambda p: p.name)
def test_example_runs_cleanly(script: Path, emu_port: int) -> None:
    proc = subprocess.run(
        [
            sys.executable,
            str(script),
            "--host",
            "127.0.0.1",
            "--port",
            str(emu_port),
            "--seconds",
            "1.2",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.strip(), "examples should print something"


def test_examples_exist() -> None:
    names = {p.name for p in EXAMPLES}
    assert names == {
        "read_tags.py",
        "tagfocus_dock_door.py",
        "mode_shootout.py",
        "antenna_watch.py",
    }
