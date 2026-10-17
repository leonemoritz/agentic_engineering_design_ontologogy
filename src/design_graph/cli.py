# SPDX-License-Identifier: Apache-2.0
"""Command-line entry point for Design Intent Graph."""

from __future__ import annotations

import json
import sys
from pathlib import Path

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


@main.command("view")
@click.argument("graph_json", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("-o", "--output", type=click.Path(dir_okay=False, path_type=Path))
def view_graph(graph_json: Path, output: Path | None) -> None:
    """Generate a self-contained HTML viewer from GRAPH_JSON."""

    from design_graph.interchange.viewer import render_viewer

    try:
        graph_data = json.loads(graph_json.read_text(encoding="utf-8"))
        html = render_viewer(graph_data)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        raise click.ClickException(str(error)) from error
    target = output or graph_json.with_suffix(".html")
    target.write_text(html, encoding="utf-8")
    click.echo(str(target))


@main.command("requirements")
@click.argument("requirement_file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def check_requirement_file(requirement_file: Path) -> None:
    """Import cards from JSON, YAML, or ReqIF and check their fidelity."""

    from design_graph.interchange.requirement_io import load_requirement_cards
    from design_graph.model.cards import check_fidelity

    try:
        cards = load_requirement_cards(requirement_file)
    except (OSError, ValueError) as error:
        raise click.ClickException(str(error)) from error
    issues = [issue for card in cards for issue in check_fidelity(card)]
    click.echo(f"Loaded {len(cards)} requirement cards")
    if issues:
        for issue in issues:
            click.echo(f"ERROR: {issue}", err=True)
        raise click.exceptions.Exit(1)
    click.echo("Requirement fidelity: passed")


@main.command("run")
@click.argument("project_yaml", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--no-solvers", is_flag=True, help="Run the analytic synthetic slice only.")
@click.option(
    "--pdf", "make_pdf", is_flag=True, help="Export report.pdf using the optional pdf extra."
)
@click.option(
    "--step",
    "make_step",
    is_flag=True,
    help="Export STEP and mass properties using the optional cad extra.",
)
@click.option(
    "-o",
    "--output-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("build/design_graph"),
)
def run_project(
    project_yaml: Path,
    no_solvers: bool,
    make_pdf: bool,
    make_step: bool,
    output_dir: Path,
) -> None:
    """Evaluate the synthetic enclosure graph and write review artifacts."""

    del no_solvers  # Solver tiers are opt-in and are not part of this baseline graph.
    from design_graph.domains.enclosure import build_enclosure_graph, load_project
    from design_graph.engineering.geometry import calculate_mass_properties, export_step
    from design_graph.interchange.report import write_review_package
    from design_graph.interchange.viewer import render_viewer

    try:
        project = load_project(project_yaml)
        graph = build_enclosure_graph(project)
        graph.check()
        evaluated = graph.evaluate(["verification", "draft"])
        result = evaluated["verification"]
        output_dir.mkdir(parents=True, exist_ok=True)
        graph_path = output_dir / "graph.json"
        graph.to_json(graph_path)
        matrix_path = output_dir / "verification.json"
        matrix_path.write_text(
            json.dumps([row.model_dump(mode="json") for row in result], indent=2) + "\n",
            encoding="utf-8",
        )
        viewer_path = output_dir / "graph.html"
        graph_data = graph.to_dict()
        viewer_path.write_text(render_viewer(graph_data), encoding="utf-8")
        if make_step:
            if project.material_density_kg_m3 is None:
                raise ValueError("STEP/mass export requires project.material_density_kg_m3")
            region = evaluated["draft"].regions[0]
            export_step(
                region,
                project.layout.base_thickness_mm,
                output_dir / "enclosure_base.step",
            )
            properties = calculate_mass_properties(
                region,
                project.layout.base_thickness_mm,
                project.material_density_kg_m3,
            )
            (output_dir / "mass_properties.json").write_text(
                json.dumps(properties.model_dump(mode="json"), indent=2) + "\n",
                encoding="utf-8",
            )
        package_path = write_review_package(
            output_dir,
            rows=result,
            graph_data=graph_data,
            title=project.name,
            pdf=make_pdf,
            cad=make_step,
        )
    except (OSError, RuntimeError, ValueError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(f"Verification rows: {matrix_path}")
    click.echo(f"Graph data: {graph_path}")
    click.echo(f"Graph viewer: {viewer_path}")
    click.echo(f"Review package: {package_path}")
    if make_step:
        click.echo(f"STEP solid: {output_dir / 'enclosure_base.step'}")


@main.group("model")
def model_group() -> None:
    """Validate and assess an MBSE system model."""


@model_group.command("validate")
@click.argument("model_file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def model_validate(model_file: Path) -> None:
    """Report structural and traceability findings; exit 1 on any error."""

    from design_graph.model import ModelError, load_model, validate_model

    try:
        findings = validate_model(load_model(model_file))
    except ModelError as error:
        raise click.ClickException(str(error)) from error
    for finding in findings:
        click.echo(
            f"{finding.severity.upper():7} {finding.code:20} {finding.element}: {finding.message}"
        )
    errors = sum(1 for finding in findings if finding.severity == "error")
    click.echo(f"{errors} errors, {len(findings) - errors} warnings")
    if errors:
        raise click.exceptions.Exit(1)


@model_group.command("assess")
@click.argument("model_file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--json", "as_json", is_flag=True, help="Emit the full assessment as JSON.")
@click.option("--strict", is_flag=True, help="Exit 1 unless every active requirement is SATISFIED.")
def model_assess(model_file: Path, as_json: bool, strict: bool) -> None:
    """Derive verification and satisfaction status for every requirement."""

    from design_graph.model import ModelError, assess_model, load_model, validate_model

    try:
        model = load_model(model_file)
    except ModelError as error:
        raise click.ClickException(str(error)) from error
    errors = [f for f in validate_model(model) if f.severity == "error"]
    if errors:
        raise click.ClickException(
            f"model has {len(errors)} validation errors; run 'model validate' first"
        )
    assessment = assess_model(model)
    if as_json:
        click.echo(json.dumps(assessment.model_dump(mode="json"), indent=2))
    else:
        for item in assessment.requirements:
            margin = f" margin={item.margin.value:g} {item.margin.unit}" if item.margin else ""
            click.echo(
                f"{item.requirement:12} {item.satisfaction.value:14} "
                f"{item.verification.value:11}{margin}"
            )
            for reason in item.reasons:
                click.echo(f"    - {reason.code}: {reason.message}")
        click.echo(", ".join(f"{k}={v}" for k, v in sorted(assessment.summary().items())))
    if strict and not assessment.all_satisfied:
        raise click.exceptions.Exit(1)
