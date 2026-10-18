# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from design_graph.mcp import ToolFailure, Workspace, tools
from design_graph.model import basis_hash, load_model

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
BRACKET = "model_process_bracket.yaml"


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    for name in (BRACKET, "model_enclosure.yaml"):
        shutil.copy(EXAMPLES / name, tmp_path / name)
    return Workspace(tmp_path)


def test_paths_stay_below_root(ws: Workspace, tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.yaml"
    outside.write_text("format_version: 1\n", encoding="utf-8")
    for path in ("../outside.yaml", str(outside)):
        with pytest.raises(ToolFailure) as error:
            ws.model(path)
        assert error.value.code == "PATH_OUTSIDE_ROOT"
    with pytest.raises(ToolFailure) as error:
        ws.model("missing.yaml")
    assert error.value.code == "FILE_NOT_FOUND"


def test_unreadable_and_invalid_models_have_codes(ws: Workspace, tmp_path: Path) -> None:
    (tmp_path / "bad.yaml").write_text("needs: 3\n", encoding="utf-8")
    with pytest.raises(ToolFailure) as error:
        tools.validate(ws, "bad.yaml")
    assert error.value.code == "MODEL_UNREADABLE"
    (tmp_path / "dangling.yaml").write_text(
        "requirements:\n- {id: R1, text: t, subject: NOPE}\n", encoding="utf-8"
    )
    assert not tools.validate(ws, "dangling.yaml").valid
    with pytest.raises(ToolFailure) as error:
        tools.verification_status(ws, "dangling.yaml")
    assert error.value.code == "MODEL_INVALID"
    assert json.loads(error.value.to_json())["findings"]


def test_read_tools_on_the_bracket_example(ws: Workspace) -> None:
    assert tools.validate(ws, BRACKET).valid
    status = tools.process_status(ws, BRACKET)
    assert [s.id for s in status.stages][:2] == ["requirements", "concepts"]
    assert status.next_actions and not status.complete
    items = tools.open_items(ws, BRACKET, owner="quality_reviewer")
    assert items.count == len(items.items) > 0
    for item in items.items:
        assert item.owner == "quality_reviewer" and item.id == f"{item.rule}:{item.element}"
    assert tools.plan(ws, BRACKET).tasks
    assert tools.verification_status(ws, BRACKET).requirements
    assert tools.matrix(ws, BRACKET).rows
    report = tools.element(ws, BRACKET, "R1")
    assert report.collection == "requirements" and len(report.content_hash) == 64
    assert report.relationships_in or report.relationships_out
    with pytest.raises(ToolFailure):
        tools.element(ws, BRACKET, "NOPE")
    assert "approvals" in tools.roles().forbidden_for_agents


def _assumption(author: str) -> dict[str, Any]:
    return {"id": "A-MCP", "statement": "Room temperature service.", "owner": author}


def test_propose_change_is_a_dry_run(ws: Workspace, tmp_path: Path) -> None:
    before = (tmp_path / BRACKET).read_bytes()
    author = "agent-requirements_engineer"
    report = tools.propose_change(
        ws, BRACKET, "requirements_engineer", {"assumptions": [_assumption(author)]}
    )
    assert report.accepted and report.changed == ["A-MCP"] and report.author == author
    assert any(i.element == "A-MCP" for i in report.new_items)  # no closure plan yet
    assert (tmp_path / BRACKET).read_bytes() == before


@pytest.mark.parametrize(
    ("role", "upserts", "detail"),
    [
        ("requirements_engineer", {"approvals": [{"id": "AP"}]}, "may not write approvals"),
        ("concept_engineer", {"assumptions": [{"id": "A"}]}, "may not write assumptions"),
    ],
)
def test_propose_change_enforces_role_permissions(
    ws: Workspace, role: str, upserts: dict[str, list[dict[str, Any]]], detail: str
) -> None:
    report = tools.propose_change(ws, BRACKET, role, upserts)
    assert report.outcome == "rejected_permission" and detail in report.detail


def test_agents_cannot_file_evidence_as_someone_else(ws: Workspace, tmp_path: Path) -> None:
    model = load_model(tmp_path / BRACKET)
    case = model.cases[0]
    evidence = {
        "id": "EV-MCP",
        "case": case.id,
        "requirement": "R1",
        "created_by": "eng",
        "date": "2026-02-01",
        "basis_hash": basis_hash(model, "R1", case.id),
    }
    report = tools.propose_change(ws, BRACKET, "structures_analyst", {"evidence": [evidence]})
    assert report.outcome == "rejected_permission" and "created by" in report.detail


def test_unknown_role(ws: Workspace) -> None:
    for role in ("human", "nobody"):
        with pytest.raises(ToolFailure) as error:
            tools.propose_change(ws, BRACKET, role, {})
        assert error.value.code == "UNKNOWN_ROLE"


def test_server_over_the_protocol(ws: Workspace) -> None:
    pytest.importorskip("mcp")
    import anyio
    from mcp import Client

    from design_graph.mcp.server import build_server

    async def scenario() -> None:
        async with Client(build_server(ws.root)) as client:
            listed = (await client.list_tools()).tools
            assert {t.name for t in listed} == {
                "validate_model",
                "get_verification_status",
                "get_verification_matrix",
                "get_process_status",
                "list_open_items",
                "plan_tasks",
                "list_roles",
                "get_element",
                "propose_change",
            }
            for tool in listed:
                assert tool.output_schema is not None
                assert tool.annotations is not None and tool.annotations.read_only_hint
            result = await client.call_tool("get_process_status", {"model_path": BRACKET})
            assert not result.is_error
            assert result.structured_content is not None
            assert result.structured_content["stages"][0]["id"] == "requirements"
            failed = await client.call_tool("validate_model", {"model_path": "../x.yaml"})
            assert failed.is_error
            text = failed.content[0].text  # type: ignore[union-attr]
            assert json.loads(text[text.index("{") :])["code"] == "PATH_OUTSIDE_ROOT"

    anyio.run(scenario)
