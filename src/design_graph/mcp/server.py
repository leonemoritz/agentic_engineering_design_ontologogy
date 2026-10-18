# SPDX-License-Identifier: Apache-2.0
"""MCP server: registers the tools in `design_graph.mcp.tools` and runs over stdio.

Thin by design: every tool is a call into `tools`, and an anticipated failure becomes a tool error
whose text is a JSON object with a `code`. All tools are read-only; `propose_change` is a dry run.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import click
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from design_graph.mcp import tools
from design_graph.mcp.tools import (
    ElementReport,
    MatrixReport,
    OpenItemsReport,
    PlanReport,
    ProcessReport,
    ProposalReport,
    RolesReport,
    ToolFailure,
    ValidationReport,
    VerificationReport,
    Workspace,
)

INSTRUCTIONS = """Design Intent Graph: an MBSE system model (YAML) with derived verification status
and a process layer that turns missing work into open items.

Paths are relative to the server's workspace root. Typical loop:
1. get_process_status -> which stage is actionable and what blocks it.
2. list_open_items (owner=<role>) or plan_tasks -> the work for one role, with a brief.
3. get_element -> read what an item points at.
4. propose_change (role, upserts) -> dry run through the permission and validation gate; shows the
   items it resolves and the new items it raises. Nothing is written.
Status is derived, never asserted. You cannot approve, attest, declare tools or create baselines;
items owned by 'human' are for a person."""

READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)


def _run[T](call: Callable[[], T]) -> T:
    try:
        return call()
    except ToolFailure as failure:
        raise ToolError(failure.to_json()) from failure


def build_server(root: str | Path = ".") -> MCPServer:
    ws = Workspace(root)
    server: MCPServer = MCPServer("design-graph", instructions=INSTRUCTIONS)

    @server.tool(annotations=READ_ONLY)
    def validate_model(model_path: str) -> ValidationReport:
        """Structural and traceability findings for a model file; any error blocks assessment."""
        return _run(lambda: tools.validate(ws, model_path))

    @server.tool(annotations=READ_ONLY)
    def get_verification_status(model_path: str) -> VerificationReport:
        """Derived verification and satisfaction status per requirement, with margins."""
        return _run(lambda: tools.verification_status(ws, model_path))

    @server.tool(annotations=READ_ONLY)
    def get_verification_matrix(model_path: str) -> MatrixReport:
        """One row per (requirement, verification case) with method, level, status and evidence."""
        return _run(lambda: tools.matrix(ws, model_path))

    @server.tool(annotations=READ_ONLY)
    def get_process_status(
        model_path: str, template_path: str | None = None, limit: int = 20
    ) -> ProcessReport:
        """Each stage's derived state, the next actions in work order, and items for a person."""
        return _run(lambda: tools.process_status(ws, model_path, template_path, limit))

    @server.tool(annotations=READ_ONLY)
    def list_open_items(
        model_path: str,
        template_path: str | None = None,
        owner: str | None = None,
        include_warnings: bool = False,
    ) -> OpenItemsReport:
        """Open items with stage, severity, owner role and the action to take."""
        return _run(
            lambda: tools.open_items(ws, model_path, template_path, owner, include_warnings)
        )

    @server.tool(annotations=READ_ONLY)
    def plan_tasks(model_path: str, template_path: str | None = None) -> PlanReport:
        """The tasks the orchestrator would hand to each agent role next, with their briefs."""
        return _run(lambda: tools.plan(ws, model_path, template_path))

    @server.tool(annotations=READ_ONLY)
    def list_roles() -> RolesReport:
        """Agent roles, what each may write, their checklists, and what no agent may write."""
        return tools.roles()

    @server.tool(annotations=READ_ONLY)
    def get_element(model_path: str, element_id: str) -> ElementReport:
        """One element with its collection, content hash, approval state and relationships."""
        return _run(lambda: tools.element(ws, model_path, element_id))

    @server.tool(annotations=READ_ONLY)
    def propose_change(
        model_path: str,
        role: str,
        upserts: dict[str, list[dict[str, Any]]],
        rationale: str = "",
        template_path: str | None = None,
    ) -> ProposalReport:
        """Dry-run a proposal as an agent role: `upserts` maps a collection to full elements (with
        `id`). Reports the gate outcome, changed ids, open items resolved and raised, and
        requirement status changes. Never writes the model."""
        return _run(
            lambda: tools.propose_change(ws, model_path, role, upserts, rationale, template_path)
        )

    return server


@click.command()
@click.option(
    "--root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=Path("."),
    show_default=True,
    help="Workspace root; tools read files only below it.",
)
def main(root: Path) -> None:
    """Run the Design Intent Graph MCP server over stdio."""

    build_server(root).run("stdio")
