"""GPIO: state readback, output control, and GPI events."""

from __future__ import annotations

import asyncio
import contextlib

from llrpkit.protocol import messages, params
from llrpkit.reader import Reader
from tests.test_hardening import make_emulator


async def test_gpio_defaults_and_output_roundtrip() -> None:
    async with make_emulator() as emu, Reader("127.0.0.1", emu.port) as reader:
        state = await reader.get_gpio()
        assert state.gpis == {1: "low", 2: "low", 3: "low", 4: "low"}
        assert state.gpos == {1: False, 2: False, 3: False, 4: False}
        await reader.set_gpo(2, True)
        state = await reader.get_gpio()
        assert state.gpos[2] is True
        assert state.gpos[1] is False
        await reader.set_gpo(2, False)
        assert (await reader.get_gpio()).gpos[2] is False


async def test_gpi_level_and_disable_show_correctly() -> None:
    async with make_emulator() as emu, Reader("127.0.0.1", emu.port) as reader:
        await emu.set_gpi(3, True)
        assert (await reader.get_gpio()).gpis[3] == "high"
        await reader.set_gpi_enabled(3, False)
        assert (await reader.get_gpio()).gpis[3] == "disabled"
        await reader.set_gpi_enabled(3, True)
        assert (await reader.get_gpio()).gpis[3] == "high"


async def test_gpi_edge_delivers_an_event() -> None:
    async with make_emulator() as emu, Reader("127.0.0.1", emu.port) as reader:
        seen: list[tuple[int, bool]] = []

        async def watch() -> None:
            async for msg in reader.events():
                if isinstance(msg, messages.READER_EVENT_NOTIFICATION):
                    gpi = msg.reader_event_notification_data.gpi_event
                    if gpi is not None:
                        seen.append((gpi.gpi_port_number, bool(gpi.gpi_event)))
                        return

        task = asyncio.create_task(watch())
        await asyncio.sleep(0.1)
        await emu.set_gpi(1, True)
        done, _ = await asyncio.wait({task}, timeout=3.0)
        assert done, "GPI event never arrived"
        assert seen == [(1, True)]


async def test_disabled_gpi_stays_silent() -> None:
    async with make_emulator() as emu, Reader("127.0.0.1", emu.port) as reader:
        await reader.set_gpi_enabled(2, False)
        await emu.set_gpi(2, True)  # no event may be emitted
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(0.5):
                async for msg in reader.events():
                    if isinstance(msg, messages.READER_EVENT_NOTIFICATION):
                        assert msg.reader_event_notification_data.gpi_event is None


async def test_invalid_gpo_port_is_a_status_error() -> None:
    import pytest

    from llrpkit.client import check_status
    from llrpkit.exceptions import LLRPStatusError

    async with make_emulator() as emu, Reader("127.0.0.1", emu.port) as reader:
        response = await reader.client.transact(
            messages.SET_READER_CONFIG(
                reset_to_factory_default=False,
                gpo_write_datas=[params.GPOWriteData(gpo_port_number=9, gpo_data=True)],
            )
        )
        with pytest.raises(LLRPStatusError, match="no GPO port"):
            check_status(response)


def test_cli_gpio_sets_and_reports() -> None:
    from typer.testing import CliRunner

    from llrpkit.cli import app
    from tests.test_cli_e2e import EmulatorThread

    runner = CliRunner()
    with EmulatorThread() as emu:
        result = runner.invoke(app, ["gpio", "127.0.0.1", "--port", str(emu.port), "--set", "1=on"])
    assert result.exit_code == 0, result.output
    assert "GPO 1 -> on" in result.output
    assert "GPO 1: on" in result.output
    assert "GPI 1: low" in result.output
