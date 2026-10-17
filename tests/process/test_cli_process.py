# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from design_graph.cli import main

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "model_process_bracket.yaml"


def test_process_status_open_items_plan_and_roles() -> None:
    runner = CliRunner()
    status = runner.invoke(main, ["process", "status", str(EXAMPLE)])
    assert status.exit_code == 0, status.output
    assert "concepts       in_progress" in status.output
    assert "PR-REVIEW TS1" in status.output

    items = runner.invoke(main, ["process", "open-items", str(EXAMPLE), "--owner", "human"])
    assert items.exit_code == 0 and "PR-APPROVAL" in items.output

    plan = runner.invoke(main, ["process", "plan", str(EXAMPLE)])
    assert plan.exit_code == 0 and "quality_reviewer" in plan.output

    roles = runner.invoke(main, ["process", "roles"])
    assert roles.exit_code == 0 and "structures_analyst" in roles.output

    as_json = runner.invoke(main, ["process", "status", str(EXAMPLE), "--json"])
    assert as_json.exit_code == 0 and '"template": "engineering-v"' in as_json.output
