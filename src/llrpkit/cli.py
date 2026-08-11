"""llrpkit command-line interface.

Live commands: ``inventory`` and ``capabilities`` speak LLRP to a reader (or
the emulator); ``emulate`` runs the built-in emulator on a port; ``dashboard``
and ``demo`` serve the web UI. ``inventory --mqtt-broker`` publishes the tag
stream to an MQTT broker instead of the terminal (``mqtt`` extra).
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Annotated, Any

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


def _parse_broker(value: str) -> tuple[str, int]:
    """Parse ``host`` or ``host:port`` for --mqtt-broker."""
    host, _, port_text = value.partition(":")
    if not host:
        raise typer.BadParameter(f"broker {value!r} has no hostname")
    if not port_text:
        return host, 1883
    try:
        return host, int(port_text)
    except ValueError as exc:
        raise typer.BadParameter(f"broker port {port_text!r} is not an integer") from exc


def _require_mqtt() -> None:
    try:
        import aiomqtt  # noqa: F401
    except ImportError as exc:  # pragma: no cover - depends on install flavor
        typer.echo('MQTT publishing needs the "mqtt" extra:\n\n    pip install "llrpkit[mqtt]"\n')
        raise typer.Exit(1) from exc


async def _run_inventory_mqtt(
    host: str,
    port: int,
    broker: tuple[str, int],
    base_topic: str | None,
    qos: int,
    username: str | None,
    password: str | None,
    inventory_kwargs: dict[str, Any],
    *,
    publish_events: bool = False,
    depart_after: float = 2.0,
) -> int:
    from llrpkit.mqtt import MQTTBridge
    from llrpkit.reader import Reader

    async with Reader(host, port) as reader:
        typer.echo(
            f"connected: model {reader.model_number}, firmware {reader.firmware!r}, "
            f"{reader.max_antennas} antenna ports"
            + (" (Octane extensions on)" if reader.impinj_extensions_enabled else "")
        )
        bridge = MQTTBridge(
            broker[0],
            broker[1],
            base_topic=base_topic if base_topic is not None else f"llrpkit/{host}",
            username=username,
            password=password,
            qos=qos,
            publish_events=publish_events,
            depart_after=depart_after,
        )
        typer.echo(
            f"publishing → mqtt://{broker[0]}:{broker[1]}  "
            f"tags on {bridge.tags_topic!r}, status on {bridge.status_topic!r}"
        )
        published = await bridge.run(reader, **inventory_kwargs)
    typer.echo(f"{published} tag report(s) published")
    return published


def _parse_antennas(value: str) -> list[int]:
    value = value.strip()
    if not value or value == "0":
        return []
    try:
        return [int(part) for part in value.split(",") if part.strip()]
    except ValueError as exc:
        raise typer.BadParameter(f"antenna list {value!r} is not comma-separated integers") from exc


async def _run_events_mode(
    host: str, port: int, inventory_kwargs: dict[str, Any], depart_after: float
) -> None:
    from contextlib import aclosing

    from llrpkit.presence import PresenceTracker, ticked_stream
    from llrpkit.reader import Reader

    tracker = PresenceTracker(depart_after=depart_after)
    async with Reader(host, port) as reader:
        typer.echo(
            f"connected: model {reader.model_number} — presence events "
            f"(depart after {depart_after:.1f}s of silence)"
        )
        ticked = ticked_stream(reader.inventory(**inventory_kwargs))
        async with aclosing(ticked):
            async for tag in ticked:
                events = list(tracker.observe(tag)) if tag is not None else []
                events.extend(tracker.check())
                for event in events:
                    stamp = time.strftime("%H:%M:%S")
                    if event.kind == "arrived":
                        typer.echo(
                            f"{stamp}  + arrived   {event.epc_hex}  ant {event.antenna}"
                            f"   ({len(tracker.present)} present)"
                        )
                    else:
                        dwell = f"{event.dwell_s:.1f}s" if event.dwell_s is not None else "-"
                        typer.echo(
                            f"{stamp}  - departed  {event.epc_hex}  dwell {dwell}"
                            f"  {event.reads} reads   ({len(tracker.present)} present)"
                        )


async def _run_inventory(
    host: str, port: int, inventory_kwargs: dict[str, Any], *, show_decode: bool = False
) -> int:
    from contextlib import aclosing

    from llrpkit.epc import decode_epc
    from llrpkit.reader import Reader

    seen = 0
    async with Reader(host, port) as reader:
        typer.echo(
            f"connected: model {reader.model_number}, firmware {reader.firmware!r}, "
            f"{reader.max_antennas} antenna ports"
            + (" (Octane extensions on)" if reader.impinj_extensions_enabled else "")
        )
        stream = reader.inventory(**inventory_kwargs)
        async with aclosing(stream):
            async for tag in stream:
                seen += 1
                rssi = f"{tag.rssi_dbm:7.2f} dBm" if tag.rssi_dbm is not None else "     --    "
                extra = ""
                if tag.phase_deg is not None:
                    extra += f"  phase {tag.phase_deg:6.1f}°"
                if tag.tid is not None:
                    extra += f"  tid {tag.tid.hex()}"
                if show_decode:
                    decoded = decode_epc(tag.epc)
                    if decoded is not None:
                        extra += f"  |  {decoded.gs1 or decoded.pure_identity_uri}"
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
    filter_epc: Annotated[
        str | None,
        typer.Option(help="Only inventory tags whose EPC starts with this hex prefix."),
    ] = None,
    filter_action: Annotated[
        str,
        typer.Option(help='With --filter-epc: "include" (default) or "exclude" matches.'),
    ] = "include",
    decode: Annotated[
        bool, typer.Option("--decode", help="Decode GS1 EPCs (GTIN/SSCC/...) inline.")
    ] = False,
    events: Annotated[
        bool,
        typer.Option("--events", help="Report arrive/depart edges instead of every read."),
    ] = False,
    depart_after: Annotated[
        float,
        typer.Option(help="With --events / --mqtt-events: silence meaning 'gone', seconds."),
    ] = 2.0,
    mqtt_events: Annotated[
        bool,
        typer.Option(
            "--mqtt-events",
            help="With --mqtt-broker: also publish arrive/depart events.",
        ),
    ] = False,
    mqtt_broker: Annotated[
        str | None,
        typer.Option(
            help='Publish reads to this MQTT broker ("host" or "host:port") '
            "instead of the terminal."
        ),
    ] = None,
    mqtt_topic: Annotated[
        str | None,
        typer.Option(help="Base MQTT topic (default: llrpkit/<reader-host>)."),
    ] = None,
    mqtt_qos: Annotated[int, typer.Option(min=0, max=2, help="QoS for tag messages.")] = 0,
    mqtt_username: Annotated[str | None, typer.Option(help="MQTT username.")] = None,
    mqtt_password: Annotated[str | None, typer.Option(help="MQTT password.")] = None,
) -> None:
    """Stream a live tag inventory to the terminal, or to an MQTT broker."""
    if search_mode is not None and search_mode.lower() not in SEARCH_MODES:
        raise typer.BadParameter(f"search mode must be one of {', '.join(SEARCH_MODES)}")
    if duration is None and count is None:
        typer.echo("(no --duration or --count given: streaming until Ctrl-C)")
    if filter_action not in ("include", "exclude"):
        raise typer.BadParameter('filter action must be "include" or "exclude"')
    if filter_epc is not None:
        try:
            bytes.fromhex(filter_epc)
        except ValueError as exc:
            raise typer.BadParameter(f"--filter-epc {filter_epc!r} is not valid hex") from exc
    mode_value = SEARCH_MODES[search_mode.lower()] if search_mode is not None else None
    inventory_kwargs: dict[str, Any] = {
        "antennas": _parse_antennas(antennas),
        "session": session,
        "search_mode": mode_value,
        "mode_index": mode,
        "tx_power_dbm": power,
        "tag_population": population,
        "epc_filter": filter_epc,
        "filter_action": filter_action,
        "include_phase": phase,
        "include_tid": tid,
        "duration": duration,
        "max_tags": count,
    }
    if mqtt_broker is not None:
        _require_mqtt()
        import aiomqtt

        try:
            with contextlib.suppress(KeyboardInterrupt):
                asyncio.run(
                    _run_inventory_mqtt(
                        host,
                        port,
                        _parse_broker(mqtt_broker),
                        mqtt_topic,
                        mqtt_qos,
                        mqtt_username,
                        mqtt_password,
                        inventory_kwargs,
                        publish_events=mqtt_events,
                        depart_after=depart_after,
                    )
                )
        except aiomqtt.MqttError as exc:
            typer.echo(f"MQTT error: {exc}")
            raise typer.Exit(1) from exc
        return
    if events:
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(_run_events_mode(host, port, inventory_kwargs, depart_after))
        return
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_run_inventory(host, port, inventory_kwargs, show_decode=decode))


@app.command()
def decode(
    epcs: Annotated[list[str], typer.Argument(help="One or more EPCs in hex.")],
) -> None:
    """Decode GS1 EPC encodings (SGTIN/SSCC/SGLN/GRAI/GIAI/GID-96)."""
    from llrpkit.epc import decode_epc

    failed = False
    for value in epcs:
        try:
            decoded = decode_epc(value)
        except ValueError:
            decoded = None
        if decoded is None:
            typer.echo(f"{value}: not a GS1 96-bit EPC encoding")
            failed = True
            continue
        typer.echo(f"{value}:")
        typer.echo(f"  scheme    {decoded.scheme}")
        typer.echo(f"  tag URI   {decoded.tag_uri}")
        typer.echo(f"  identity  {decoded.pure_identity_uri}")
        for key, field_value in decoded.fields.items():
            typer.echo(f"  {key:<18} {field_value}")
        if decoded.gs1:
            typer.echo(f"  GS1       {decoded.gs1}")
    if failed:
        raise typer.Exit(1)


def _parse_hex(value: str, flag: str) -> bytes:
    try:
        return bytes.fromhex(value)
    except ValueError as exc:
        raise typer.BadParameter(f"{flag} {value!r} is not valid hex") from exc


def _echo_access(result: Any) -> None:
    from llrpkit.reader import AccessResult

    assert isinstance(result, AccessResult)
    if result.ok:
        detail = ""
        if result.data is not None:
            detail = f"  data {result.data.hex()}"
        if result.words_written is not None:
            detail = f"  {result.words_written} word(s) written"
        typer.echo(f"OK  tag {result.epc_hex}{detail}")
    else:
        typer.echo(f"FAILED ({result.status})  tag {result.epc_hex}")
        raise typer.Exit(1)


@app.command()
def read(
    host: Annotated[str, typer.Argument(help="Reader hostname or IP.")],
    port: Annotated[int, typer.Option(help="LLRP port.")] = 5084,
    bank: Annotated[str, typer.Option(help="Memory bank: reserved, epc, tid, or user.")] = "user",
    word_pointer: Annotated[int, typer.Option(help="First word to read.")] = 0,
    words: Annotated[int, typer.Option(help="Words to read (0 = to end of bank).")] = 0,
    epc: Annotated[
        str | None, typer.Option(help="Target tag EPC (hex prefix); omit for any tag.")
    ] = None,
    password: Annotated[int, typer.Option(help="Access password, if the tag has one.")] = 0,
    timeout: Annotated[float, typer.Option(help="Give up after this many seconds.")] = 8.0,
) -> None:
    """Read tag memory (a Gen2 READ via an AccessSpec)."""
    from llrpkit.reader import Reader

    target = _parse_hex(epc, "--epc") if epc is not None else None

    async def run() -> None:
        async with Reader(host, port) as reader:
            _echo_access(
                await reader.read_memory(
                    bank=bank,
                    word_pointer=word_pointer,
                    word_count=words,
                    target_epc=target,
                    access_password=password,
                    timeout=timeout,
                )
            )

    asyncio.run(run())


@app.command()
def write(
    host: Annotated[str, typer.Argument(help="Reader hostname or IP.")],
    data: Annotated[str, typer.Option(help="Hex data to write (whole words).")],
    port: Annotated[int, typer.Option(help="LLRP port.")] = 5084,
    bank: Annotated[str, typer.Option(help="Memory bank: reserved, epc, tid, or user.")] = "user",
    word_pointer: Annotated[int, typer.Option(help="First word to write.")] = 0,
    epc: Annotated[
        str | None, typer.Option(help="Target tag EPC (hex prefix); omit for any tag.")
    ] = None,
    password: Annotated[int, typer.Option(help="Access password, if the tag has one.")] = 0,
    timeout: Annotated[float, typer.Option(help="Give up after this many seconds.")] = 8.0,
) -> None:
    """Write tag memory (a Gen2 WRITE via an AccessSpec)."""
    from llrpkit.reader import Reader

    payload = _parse_hex(data, "--data")
    target = _parse_hex(epc, "--epc") if epc is not None else None

    async def run() -> None:
        async with Reader(host, port) as reader:
            _echo_access(
                await reader.write_memory(
                    bank=bank,
                    word_pointer=word_pointer,
                    data=payload,
                    target_epc=target,
                    access_password=password,
                    timeout=timeout,
                )
            )

    asyncio.run(run())


@app.command(name="write-epc")
def write_epc(
    host: Annotated[str, typer.Argument(help="Reader hostname or IP.")],
    new_epc: Annotated[str, typer.Option(help="The new EPC, in hex (same length).")],
    port: Annotated[int, typer.Option(help="LLRP port.")] = 5084,
    epc: Annotated[
        str | None,
        typer.Option(help="Target tag EPC (hex). Strongly recommended with >1 tag in field."),
    ] = None,
    password: Annotated[int, typer.Option(help="Access password, if the tag has one.")] = 0,
    timeout: Annotated[float, typer.Option(help="Give up after this many seconds.")] = 8.0,
    yes: Annotated[bool, typer.Option("--yes", help="Skip the confirmation prompt.")] = False,
) -> None:
    """Rewrite a tag's EPC — the tag is re-labeled for every reader after this."""
    from llrpkit.reader import Reader

    new = _parse_hex(new_epc, "--new-epc")
    target = _parse_hex(epc, "--epc") if epc is not None else None
    if target is None and not yes:
        typer.confirm(
            "No --epc target given: the FIRST tag that answers will be re-labeled. Continue?",
            abort=True,
        )

    async def run() -> None:
        async with Reader(host, port) as reader:
            _echo_access(
                await reader.write_epc(
                    new, target_epc=target, access_password=password, timeout=timeout
                )
            )

    asyncio.run(run())


@app.command()
def gpio(
    host: Annotated[str, typer.Argument(help="Reader hostname or IP.")],
    port: Annotated[int, typer.Option(help="LLRP port.")] = 5084,
    set_: Annotated[
        list[str] | None,
        typer.Option("--set", help="Drive a GPO, e.g. --set 1=on --set 2=off (repeatable)."),
    ] = None,
    enable_gpi: Annotated[
        int | None, typer.Option(help="Enable this GPI port's configuration.")
    ] = None,
    disable_gpi: Annotated[
        int | None, typer.Option(help="Disable this GPI port's configuration.")
    ] = None,
) -> None:
    """Show GPI/GPO state; optionally drive outputs or (un)configure inputs."""
    from llrpkit.reader import Reader

    changes: list[tuple[int, bool]] = []
    for item in set_ or []:
        port_text, _, value = item.partition("=")
        try:
            gpo_port = int(port_text)
        except ValueError as exc:
            raise typer.BadParameter(f"--set {item!r}: port is not an integer") from exc
        if value.lower() not in ("on", "off", "1", "0", "true", "false", "high", "low"):
            raise typer.BadParameter(f"--set {item!r}: value must be on/off")
        changes.append((gpo_port, value.lower() in ("on", "1", "true", "high")))

    async def run() -> None:
        async with Reader(host, port) as reader:
            for gpo_port, state in changes:
                await reader.set_gpo(gpo_port, state)
                typer.echo(f"GPO {gpo_port} -> {'on' if state else 'off'}")
            if enable_gpi is not None:
                await reader.set_gpi_enabled(enable_gpi, True)
                typer.echo(f"GPI {enable_gpi} configuration enabled")
            if disable_gpi is not None:
                await reader.set_gpi_enabled(disable_gpi, False)
                typer.echo(f"GPI {disable_gpi} configuration disabled")
            state_now = await reader.get_gpio()
            for gpi_port in sorted(state_now.gpis):
                typer.echo(f"GPI {gpi_port}: {state_now.gpis[gpi_port]}")
            for gpo_port in sorted(state_now.gpos):
                typer.echo(f"GPO {gpo_port}: {'on' if state_now.gpos[gpo_port] else 'off'}")

    asyncio.run(run())


async def _run_modes(host: str, port: int, dense: bool, fast: bool) -> None:
    from llrpkit.modes import suggest_mode
    from llrpkit.reader import Reader

    async with Reader(host, port) as reader:
        annotated = reader.annotated_modes()
        typer.echo(f"{len(annotated)} RF modes reported by {host}:\n")
        for mode in annotated:
            rf = mode.rf
            miller = "FM0" if rf.m_value == 0 else f"Miller-{2**rf.m_value}"
            tag = "autoset" if mode.is_autoset else "fixed"
            typer.echo(
                f"mode {mode.mode_id:>5}  {mode.name}  [{tag}, {miller}, {rf.bdr_value} bps]"
            )
            typer.echo(f"       {mode.summary}\n")
        pick, reason = suggest_mode(
            reader.capabilities.modes, dense_environment=dense, prioritize_speed=fast
        )
        typer.echo(f"suggestion: mode {pick.mode_id} ({pick.name}) — {reason}")


@app.command()
def modes(
    host: Annotated[str, typer.Argument(help="Reader hostname or IP.")],
    port: Annotated[int, typer.Option(help="LLRP port.")] = 5084,
    dense: Annotated[
        bool, typer.Option(help="Suggest for a dense multi-reader environment.")
    ] = False,
    fast: Annotated[bool, typer.Option(help="Suggest prioritizing throughput.")] = False,
) -> None:
    """Show the reader's RF mode table with llrpkit's curated guidance."""
    asyncio.run(_run_modes(host, port, dense, fast))


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
        temperature = await reader.get_temperature()
        if temperature is not None:
            typer.echo(f"temperature       {temperature:.0f} °C")
        typer.echo(f"RF modes          {len(caps.modes)}  (see `llrpkit modes`)")
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


def _require_dashboard() -> None:
    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError as exc:  # pragma: no cover - depends on install flavor
        typer.echo(
            "The dashboard needs the 'dashboard' extra:\n\n    pip install \"llrpkit[dashboard]\"\n"
        )
        raise typer.Exit(1) from exc


async def _serve(app: Any, host: str, port: int) -> None:
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))
    await server.serve()


@app.command()
def dashboard(
    host: Annotated[str, typer.Option(help="Bind address (localhost by default).")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="HTTP port.")] = 8000,
) -> None:
    """Run the web dashboard; add readers from the UI."""
    _require_dashboard()
    from llrpkit.dashboard import create_app

    typer.echo(f"llrpkit dashboard → http://{host}:{port}  (Ctrl-C to stop)")
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_serve(create_app(), host, port))


@app.command()
def demo(
    host: Annotated[str, typer.Option(help="Bind address (localhost by default).")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="HTTP port.")] = 8000,
    tags: Annotated[int, typer.Option(help="Synthetic tag population size.")] = 16,
    rate: Annotated[float, typer.Option(help="Approximate tag reads per second.")] = 60.0,
    antennas: Annotated[int, typer.Option(help="Number of antenna ports.")] = 4,
) -> None:
    """The sixty-second experience: emulated reader + dashboard, zero hardware."""
    _require_dashboard()
    from llrpkit.dashboard import create_demo_app

    typer.echo(f"llrpkit demo — emulated reader + live dashboard → http://{host}:{port}")
    typer.echo("watch the Live tab, pull an antenna in Tuning, no hardware needed. Ctrl-C stops.")
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_serve(create_demo_app(tags=tags, rate=rate, antennas=antennas), host, port))


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
