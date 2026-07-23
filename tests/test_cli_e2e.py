"""CLI end-to-end tests against an emulator running in a background thread."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator

import pytest
from typer.testing import CliRunner

from llrpkit.cli import app
from llrpkit.emulator import LLRPEmulator

runner = CliRunner()


class EmulatorThread:
    """Runs the emulator in its own event loop so sync CLI code can dial it."""

    def __init__(self) -> None:
        self.port = 0
        self._ready = threading.Event()
        self._stop: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        asyncio.run(self._main())

    async def _main(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        async with LLRPEmulator(reads_per_sec=400.0) as emu:
            self.port = emu.port
            self._ready.set()
            await self._stop.wait()

    def __enter__(self) -> EmulatorThread:
        self._thread.start()
        if not self._ready.wait(timeout=5.0):
            raise RuntimeError("emulator thread did not start")
        return self

    def __exit__(self, *exc_info: object) -> None:
        assert self._loop is not None
        assert self._stop is not None
        self._loop.call_soon_threadsafe(self._stop.set)
        self._thread.join(timeout=5.0)


@pytest.fixture(name="emu_port")
def fixture_emu_port() -> Iterator[int]:
    with EmulatorThread() as emu:
        yield emu.port


def test_cli_inventory_streams_and_stops(emu_port: int) -> None:
    result = runner.invoke(
        app,
        [
            "inventory",
            "127.0.0.1",
            "--port",
            str(emu_port),
            "--count",
            "5",
            "--duration",
            "10",
            "--search-mode",
            "tagfocus",
            "--phase",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "connected: model 700" in result.output
    assert "Octane extensions on" in result.output
    assert "5 tag report(s)" in result.output
    assert result.output.count("e2000017") == 5
    assert "phase" in result.output


def test_cli_capabilities(emu_port: int) -> None:
    result = runner.invoke(app, ["capabilities", "127.0.0.1", "--port", str(emu_port)])
    assert result.exit_code == 0, result.output
    assert "(Impinj)" in result.output
    assert "model number      700" in result.output
    assert "antenna ports     4" in result.output
    assert "10.00 to 30.00 dBm" in result.output
    assert "mode  1002" in result.output


def test_cli_inventory_rejects_bad_search_mode(emu_port: int) -> None:
    result = runner.invoke(
        app, ["inventory", "127.0.0.1", "--port", str(emu_port), "--search-mode", "warp"]
    )
    assert result.exit_code != 0
    assert "search mode" in result.output


def test_cli_inventory_rejects_bad_antennas() -> None:
    result = runner.invoke(app, ["inventory", "127.0.0.1", "--antennas", "1,x", "--count", "1"])
    assert result.exit_code != 0
