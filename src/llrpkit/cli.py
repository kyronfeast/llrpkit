"""llrpkit command-line interface.

Live commands: ``inventory`` and ``capabilities`` speak LLRP to a reader (or
the emulator); ``emulate`` runs the built-in emulator on a port. The
``dashboard`` and ``demo`` commands arrive with the web UI phase.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Annotated

import typer

from llrpkit import __version__

app = typer.Typer(
    name="llrpkit",
    help="LLRP toolkit for Impinj RAIN RFID readers (R700, Speedway).",
    no_args_is_help=True,
)

SEARCH_MODES = {"reader": 0, "single": 1, "dual": 2, "tagfocus": 3}


def _print_version(value: bool) -> None:
    if value:
        typer.echo(f"llrpkit {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_print_version,
            is_eager=True,
            help="Show the llrpkit version and exit.",
        ),
    ] = False,
) -> None:
    """LLRP toolkit for Impinj RAIN RFID readers."""


@app.command()
def version() -> None:
    """Show the llrpkit version."""
    typer.echo(f"llrpkit {__version__}")


def _parse_antennas(value: str) -> list[int]:
    value = value.strip()
    if not value or value == "0":
        return []
    try:
        return [int(part) for part in value.split(",") if part.strip()]
    except ValueError as exc:
        raise typer.BadParameter(f"antenna list {value!r} is not comma-separated integers") from exc


async def _run_inventory(
    host: str,
    port: int,
    antennas: list[int],
    session: int,
    search_mode: int | None,
    mode_index: int | None,
    power: float | None,
    population: int,
    phase: bool,
    tid: bool,
    duration: float | None,
    count: int | None,
) -> int:
    from contextlib import aclosing

    from llrpkit.reader import Reader

    seen = 0
    async with Reader(host, port) as reader:
        typer.echo(
            f"connected: model {reader.model_number}, firmware {reader.firmware!r}, "
            f"{reader.max_antennas} antenna ports"
            + (" (Octane extensions on)" if reader.impinj_extensions_enabled else "")
        )
        stream = reader.inventory(
            antennas=antennas,
            session=session,
            search_mode=search_mode,
            mode_index=mode_index,
            tx_power_dbm=power,
            tag_population=population,
            include_phase=phase,
            include_tid=tid,
            duration=duration,
            max_tags=count,
        )
        async with aclosing(stream):
            async for tag in stream:
                seen += 1
                rssi = f"{tag.rssi_dbm:7.2f} dBm" if tag.rssi_dbm is not None else "     --    "
                extra = ""
                if tag.phase_deg is not None:
                    extra += f"  phase {tag.phase_deg:6.1f}°"
                if tag.tid is not None:
                    extra += f"  tid {tag.tid.hex()}"
                typer.echo(f"{tag.epc_hex}  ant {tag.antenna}  {rssi}{extra}")
    typer.echo(f"{seen} tag report(s)")
    return seen


@app.command()
def inventory(
    host: Annotated[str, typer.Argument(help="Reader hostname or IP.")],
    port: Annotated[int, typer.Option(help="LLRP port.")] = 5084,
    antennas: Annotated[
        str, typer.Option(help="Comma-separated antenna ports; 0 means all.")
    ] = "0",
    session: Annotated[int, typer.Option(min=0, max=3, help="C1G2 session (0-3).")] = 1,
    search_mode: Annotated[
        str | None,
        typer.Option(help="Impinj search mode: reader, single, dual, or tagfocus."),
    ] = None,
    mode: Annotated[int | None, typer.Option(help="RF mode index from the reader's table.")] = None,
    power: Annotated[float | None, typer.Option(help="Transmit power in dBm.")] = None,
    population: Annotated[int, typer.Option(help="Estimated tag population.")] = 32,
    phase: Annotated[bool, typer.Option(help="Include RF phase (Impinj).")] = False,
    tid: Annotated[bool, typer.Option(help="Include serialized TID (Impinj).")] = False,
    duration: Annotated[float | None, typer.Option(help="Stop after this many seconds.")] = None,
    count: Annotated[int | None, typer.Option(help="Stop after this many reports.")] = None,
) -> None:
    """Stream a live tag inventory to the terminal."""
    if search_mode is not None and search_mode.lower() not in SEARCH_MODES:
        raise typer.BadParameter(f"search mode must be one of {', '.join(SEARCH_MODES)}")
    if duration is None and count is None:
        typer.echo("(no --duration or --count given: streaming until Ctrl-C)")
    mode_value = SEARCH_MODES[search_mode.lower()] if search_mode is not None else None
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(
            _run_inventory(
                host,
                port,
                _parse_antennas(antennas),
                session,
                mode_value,
                mode,
                power,
                population,
                phase,
                tid,
                duration,
                count,
            )
        )


async def _run_capabilities(host: str, port: int) -> None:
    from llrpkit.reader import Reader

    async with Reader(host, port) as reader:
        caps = reader.capabilities
        typer.echo(
            f"manufacturer      {caps.manufacturer}" + ("  (Impinj)" if caps.is_impinj else "")
        )
        typer.echo(f"model number      {caps.model_number}")
        typer.echo(f"firmware          {caps.firmware}")
        typer.echo(f"antenna ports     {caps.max_antennas}")
        if caps.transmit_powers:
            lo = min(caps.transmit_powers.values())
            hi = max(caps.transmit_powers.values())
            typer.echo(
                f"transmit power    {lo:.2f} to {hi:.2f} dBm ({len(caps.transmit_powers)} steps)"
            )
        typer.echo(f"frequencies       {'hopping' if caps.hopping else 'fixed'}")
        typer.echo(f"RF modes          {len(caps.modes)}")
        for m in caps.modes:
            miller = 2**m.m_value if m.m_value else "FM0"
            typer.echo(f"  mode {m.mode_id:>5}  M={miller}  BDR={m.bdr_value} bps")


@app.command()
def capabilities(
    host: Annotated[str, typer.Argument(help="Reader hostname or IP.")],
    port: Annotated[int, typer.Option(help="LLRP port.")] = 5084,
) -> None:
    """Show what a reader reports about itself."""
    asyncio.run(_run_capabilities(host, port))


async def _run_emulator(port: int, tags: int, rate: float, antennas: int, seed: int) -> None:
    from llrpkit.emulator import LLRPEmulator, default_population

    emu = LLRPEmulator(
        port=port,
        tags=default_population(tags, antennas),
        reads_per_sec=rate,
        antenna_count=antennas,
        seed=seed,
    )
    async with emu:
        typer.echo(
            f"emulated Impinj-style reader on port {emu.port} "
            f"({tags} tags, {antennas} antennas, ~{rate:g} reads/s) — Ctrl-C to stop"
        )
        await asyncio.Event().wait()  # pragma: no cover - runs until interrupted


@app.command()
def emulate(
    port: Annotated[int, typer.Option(help="Port to listen on (0 picks a free one).")] = 5084,
    tags: Annotated[int, typer.Option(help="Synthetic tag population size.")] = 12,
    rate: Annotated[float, typer.Option(help="Approximate tag reads per second.")] = 50.0,
    antennas: Annotated[int, typer.Option(help="Number of antenna ports.")] = 4,
    seed: Annotated[int, typer.Option(help="Random seed for the tag world.")] = 1,
) -> None:
    """Run the built-in reader emulator (no hardware required)."""
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_run_emulator(port, tags, rate, antennas, seed))
