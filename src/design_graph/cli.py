# SPDX-License-Identifier: Apache-2.0
"""Command-line entry point for Design Intent Graph."""

from __future__ import annotations

import sys

import click


@click.group()
def main() -> None:
    """Build and inspect engineering design-intent graphs."""


@main.command()
def doctor() -> None:
    """Check the Python runtime and core package dependencies."""

    version = sys.version_info
    click.echo(f"Python: {version.major}.{version.minor}.{version.micro}")
    if version < (3, 12):
        click.echo("ERROR: Design Intent Graph requires Python 3.12 or newer.", err=True)
        raise click.exceptions.Exit(1)
    try:
        import pydantic

        click.echo(f"pydantic: {pydantic.__version__}")
    except ImportError:
        click.echo("ERROR: pydantic is not installed.", err=True)
        raise click.exceptions.Exit(1) from None
    click.echo("Core environment: ready")