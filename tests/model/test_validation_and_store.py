# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from pathlib import Path

import pytest
from helpers import DAY, approve, approved_model, base_model, evidence

from design_graph import Graph, rule
from design_graph.model import (
    Assumption,
    Need,
    Quantity,
    Relationship,
    Requirement,
    SystemModel,
    ToolDeclaration,
    VerificationCase,
    dump_model,
    load_model,
    validate_model,
)
from design_graph.model.baseline import changes_since, create_baseline, evidence_from_graph
from design_graph.model.elements import Attestation


def codes(model: SystemModel, severity: str = "error") -> set[str]:
    return {f.code for f in validate_model(model) if f.severity == severity}


def test_base_model_is_valid() -> None:
    assert codes(base_model()) == set()


def test_requirement_without_need_or_verification_is_an_error() -> None:
    model = base_model()
    model.relationships = [r for r in model.relationships if r.kind == "satisfy"]
    assert {"REQ_NO_NEED", "REQ_NO_VERIFY"} <= codes(model)


def test_parent_can_be_verified_by_decomposition() -> None:
    model = base_model()
    model.relationships = [r for r in model.relationships if r.kind != "verify"]
    model.requirements.append(
        Requirement(id="R2", text="The part shall weigh 1 kg.", subject="SYS", tbc=True)
    )
    model.relationships.append(Relationship(id="d", kind="derive", source="R2", target="R1"))
    model.relationships.append(Relationship(id="r", kind="refine", source="R2", target="N1"))
    assert "REQ_NO_VERIFY" not in {f.code for f in validate_model(model) if f.element == "R1"}


def test_derive_cycle_and_bad_endpoint_types_are_errors() -> None:
    model = base_model()
    model.requirements.append(
        Requirement(id="R2", text="The system shall be light.", subject="SYS", tbc=True)
    )
    model.relationships += [
        Relationship(id="a", kind="derive", source="R1", target="R2"),
        Relationship(id="b", kind="derive", source="R2", target="R1"),
        Relationship(id="c", kind="verify", source="R1", target="R1"),
    ]
    assert {"REL_CYCLE", "REL_TYPE"} <= codes(model)


def test_dangling_and_duplicate_ids() -> None:
    model = base_model()
    model.relationships.append(Relationship(id="x", kind="trace", source="R1", target="NOPE"))
    model.needs.append(Need(id="R1", stakeholder="dup"))
    assert {"REL_DANGLING", "DUPLICATE_ID"} <= codes(model)


def test_dimension_mismatch_between_attribute_and_target() -> None:
    model = base_model()
    model.requirements[0] = model.requirements[0].model_copy(update={"target": Quantity(1.0, "m")})
    assert "REQ_DIMENSION" in codes(model)


def test_eq_needs_tolerance_and_range_needs_upper() -> None:
    model = base_model()
    model.requirements[0] = model.requirements[0].model_copy(update={"relation": "eq"})
    assert "REQ_TOLERANCE" in codes(model)
    model.requirements[0] = model.requirements[0].model_copy(update={"relation": "range"})
    assert "REQ_RANGE" in codes(model)


def test_offset_unit_tolerance_rejected() -> None:
    model = base_model()
    model.requirements[0] = model.requirements[0].model_copy(
        update={
            "relation": "eq",
            "target": Quantity(20.0, "degC"),
            "tolerance": Quantity(1.0, "degC"),
        }
    )
    assert "REQ_OFFSET" in codes(model)


def test_vague_wording_and_missing_shall_are_warnings() -> None:
    model = base_model()
    model.requirements[0] = model.requirements[0].model_copy(
        update={"text": "Mass is adequate and/or minimal."}
    )
    found = codes(model, "warning")
    assert {"REQ_SHALL", "REQ_VAGUE"} <= found


def test_agent_cannot_approve_or_attest() -> None:
    model = approved_model()
    approve(model, "R1", who="bot")
    evidence(
        model,
        value=None,
        attestations=[Attestation(principal="bot", role="verifier", outcome="pass", date=DAY)],
    )
    assert {"APPROVAL_UNAUTHORIZED", "ATTEST_UNAUTHORIZED"} <= codes(model)


def test_tool_must_be_declared_by_human_and_case_tool_must_exist() -> None:
    model = base_model()
    model.tools.append(ToolDeclaration(id="t2", version="1", intended_use="x", declared_by="bot"))
    model.cases.append(VerificationCase(id="C9", method="A", tool="ghost"))
    assert {"TOOL_DECLARER", "CASE_TOOL"} <= codes(model)


def test_evidence_must_target_a_verified_pair() -> None:
    model = base_model()
    model.cases.append(VerificationCase(id="C2", method="A", tool="fem"))
    evidence(model, case="C2", value=Quantity(1.0, "kg"))
    assert "EVID_LINK" in codes(model)


def test_closed_assumption_needs_closure_note() -> None:
    model = base_model()
    model.assumptions.append(
        Assumption(id="A1", statement="s", owner="eng", status="closed", impacts=["R1"])
    )
    assert "ASSUME_CLOSURE" in codes(model)


def test_stale_approval_is_a_warning() -> None:
    model = approved_model()
    model.requirements[0] = model.requirements[0].model_copy(update={"name": "renamed"})
    assert "APPROVAL_STALE" in codes(model, "warning")


def test_yaml_round_trip(tmp_path: Path) -> None:
    model = approved_model()
    evidence(model, value=Quantity(2.0, "kg"))
    path = tmp_path / "model.yaml"
    dump_model(model, path)
    loaded = load_model(path)
    assert loaded.elements() == model.elements()
    second = tmp_path / "again.yaml"
    dump_model(loaded, second)
    assert second.read_text(encoding="utf-8") == path.read_text(encoding="utf-8")


def test_baseline_reports_changes_and_affected_requirements() -> None:
    model = approved_model()
    baseline = create_baseline(model, id="B1", created_by="eng", date=DAY)
    assert changes_since(model, baseline).clean
    model.parts[0] = model.parts[0].model_copy(update={"doc": "changed"})
    impact = changes_since(model, baseline)
    assert impact.modified == ["SYS"]
    assert impact.affected_requirements == ["R1"]


def test_baseline_requires_valid_model_and_human_approver() -> None:
    with pytest.raises(ValueError, match="human approver"):
        create_baseline(base_model(), id="B1", created_by="bot", date=DAY)
    broken = base_model()
    broken.relationships.clear()
    with pytest.raises(ValueError, match="validation errors"):
        create_baseline(broken, id="B1", created_by="eng", date=DAY)


def test_evidence_from_graph_carries_provenance_and_current_basis() -> None:
    @rule(version="1", source="test mass")
    def mass(density: float) -> float:
        return density * 0.001

    graph = Graph().add("mass", mass, {"density": 2000.0})
    model = approved_model()
    item = evidence_from_graph(
        model,
        graph,
        id="E1",
        case="C1",
        requirement="R1",
        created_by="bot",
        date=DAY,
        node="mass",
        unit="kg",
    )
    assert item.provenance is not None and item.provenance.node == "mass"
    model.evidence.append(item)
    from design_graph.model import SatisfactionStatus, assess_model

    assert assess_model(model).by_id()["R1"].satisfaction == SatisfactionStatus.SATISFIED
