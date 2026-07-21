"""llrpkit command-line interface.

The CLI grows one layer per roadmap phase: ``inventory`` and ``capabilities``
arrive with the client (Phase 2), ``dashboard`` and ``demo`` with the web UI
(Phase 4).
"""

from __future__ import annotations

from typing import Annotated

import typer

from llrpkit import __version__

app = typer.Typer(
    name="llrpkit",
    help="LLRP toolkit for Impinj RAIN RFID readers (R700, Speedway).",
    no_args_is_help=True,
)


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
