"""FactoryAI command line interface.

The CLI is a presentation adapter: every command resolves dependencies from the
composition root, builds a command object and invokes an application use case. No business
logic lives here.

Commands are added as their use cases land — ``ingest`` in Phase 3, ``dataset`` in Phase 4,
``train`` in Phase 5 (see ``docs/ROADMAP.md``).
"""

from __future__ import annotations

import typer

from factoryai import __version__

app = typer.Typer(
    name="factoryai",
    help="Industrial Visual Inspection Platform - command line interface.",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def main() -> None:
    """Keep the CLI a command group.

    Without an explicit callback, Typer collapses a single-command application into that
    command, so ``factoryai version`` would be parsed as an unexpected argument. The
    callback preserves the ``factoryai <command>`` shape that later phases rely on.
    """


@app.command()
def version() -> None:
    """Print the installed FactoryAI version."""
    typer.echo(__version__)


if __name__ == "__main__":  # pragma: no cover
    app()
