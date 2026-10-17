# SPDX-License-Identifier: Apache-2.0
"""Command-line entry point for Design Intent Graph."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

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


@model_group.command("matrix")
@click.argument("model_file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def model_matrix(model_file: Path) -> None:
    """Print the requirement-by-case verification matrix with derived status."""

    from design_graph.model import ModelError, load_model, validate_model
    from design_graph.model.matrix import verification_matrix

    try:
        model = load_model(model_file)
    except ModelError as error:
        raise click.ClickException(str(error)) from error
    if any(f.severity == "error" for f in validate_model(model)):
        raise click.ClickException("model has validation errors; run 'model validate' first")
    for row in verification_matrix(model):
        click.echo(
            f"{row.requirement:12} {row.satisfaction:14} {row.case:12} {row.method:2} "
            f"{row.level or '-':10} {row.stage or '-':8} {row.status:11} {row.evidence or '-'}"
        )


@model_group.command("import-cards")
@click.argument("card_file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--subject", required=True, help="Id of the part the requirements apply to.")
@click.option("--need", default="N-IMPORTED", show_default=True, help="Need id to trace to.")
@click.option("-o", "--output", required=True, type=click.Path(dir_okay=False, path_type=Path))
def model_import_cards(card_file: Path, subject: str, need: str, output: Path) -> None:
    """Import JSON/YAML/ReqIF cards as unapproved model requirements."""

    from design_graph.interchange.requirement_io import load_requirement_cards
    from design_graph.model import dump_model
    from design_graph.model.cards_import import import_cards

    try:
        model = import_cards(load_requirement_cards(card_file), subject=subject, need=need)
        dump_model(model, output)
    except (OSError, ValueError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(f"Imported {len(model.requirements)} requirements into {output}")


@main.group("process")
def process_group() -> None:
    """Process layer: stage status, open items, and agent task plans."""


def _load_for_process(model_file: Path, template_file: Path | None) -> tuple[Any, Any]:
    from design_graph.model import ModelError, load_model
    from design_graph.process import DEFAULT_TEMPLATE, load_template

    try:
        model = load_model(model_file)
        template = load_template(template_file) if template_file else DEFAULT_TEMPLATE
    except (ModelError, OSError, ValueError) as error:
        raise click.ClickException(str(error)) from error
    return model, template


_TEMPLATE_OPTION = click.option(
    "--template",
    "template_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Process template YAML (default: built-in engineering-v).",
)


@process_group.command("status")
@click.argument("model_file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@_TEMPLATE_OPTION
@click.option("--json", "as_json", is_flag=True, help="Emit the full status as JSON.")
def process_status(model_file: Path, template_file: Path | None, as_json: bool) -> None:
    """Show each stage's state and the next actions."""

    from design_graph.process import evaluate_process

    model, template = _load_for_process(model_file, template_file)
    status = evaluate_process(model, template)
    if as_json:
        click.echo(json.dumps(status.model_dump(mode="json"), indent=2))
        return
    for stage in status.stages:
        missing = f" missing={','.join(stage.missing)}" if stage.missing else ""
        click.echo(
            f"{stage.id:14} {stage.state:12} blocking={stage.blocking:<3} "
            f"warnings={stage.warnings:<3} human={stage.human_blocking}{missing}"
        )
    click.echo("next actions:")
    for item in status.next_actions()[:15]:
        click.echo(f"  [{item.owner}] {item.rule} {item.element}: {item.message}")
    click.echo(f"{len(status.human_actions)} items need a person")


@process_group.command("open-items")
@click.argument("model_file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@_TEMPLATE_OPTION
@click.option("--owner", help="Only items owned by this role (or 'human').")
@click.option("--all", "include_warnings", is_flag=True, help="Include warnings.")
def process_open_items(
    model_file: Path, template_file: Path | None, owner: str | None, include_warnings: bool
) -> None:
    """List open items with stage, owner and the action to take."""

    from design_graph.process import evaluate_process

    model, template = _load_for_process(model_file, template_file)
    status = evaluate_process(model, template)
    items = [
        i
        for i in status.items
        if (include_warnings or i.severity == "blocking") and (owner is None or i.owner == owner)
    ]
    for item in sorted(items, key=lambda i: (template.order(i.stage), i.severity, i.rule)):
        click.echo(
            f"{item.stage:13} {item.severity:8} {item.owner:22} {item.rule:22} {item.element}"
        )
        click.echo(f"    {item.message}")
        click.echo(f"    -> {item.action}")
    click.echo(f"{len(items)} items")


@process_group.command("plan")
@click.argument("model_file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@_TEMPLATE_OPTION
def process_plan(model_file: Path, template_file: Path | None) -> None:
    """Print the tasks the orchestrator would hand to each agent role next."""

    from design_graph.agents import Orchestrator

    model, template = _load_for_process(model_file, template_file)
    orchestrator = Orchestrator(model, _NoBackend(), template=template)
    tasks = orchestrator.plan()
    for task in tasks:
        click.echo(f"=== {task.id} ({task.role}, stage {task.stage}, author {task.author})")
        click.echo(task.brief)
        click.echo("")
    click.echo(f"{len(tasks)} agent tasks; items for a person are listed by 'process status'")


@process_group.command("roles")
def process_roles() -> None:
    """List the agent roles, what they may write, and their checklists."""

    from design_graph.agents import DEFAULT_ROLES

    for role in DEFAULT_ROLES.values():
        click.echo(f"{role.id} ({role.kind}): {role.title}")
        click.echo(f"    writes: {', '.join(role.writes) or '-'}")
        for item in role.checklist:
            click.echo(f"    - {item}")


class _NoBackend:
    """Planning needs no backend; executing with this one fails loudly."""

    def execute(self, task: Any, model: Any) -> Any:
        raise RuntimeError("planning only")

    def review(self, task: Any, model: Any) -> Any:
        raise RuntimeError("planning only")
