# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from pathlib import Path

import pytest
from proc_helpers import add_concepts, bracket_model, complete_model, review

from design_graph.agents import DEFAULT_ROLES
from design_graph.model import Quantity, SystemModel
from design_graph.model.engineering import (
    ConsistencyCheck,
    Credibility,
    FailureMode,
    LoadCase,
    Statement,
    ValueRef,
)
from design_graph.process import (
    DEFAULT_TEMPLATE,
    RULES,
    STAGE_OWNER,
    ProcessTemplate,
    StageTemplate,
    evaluate_process,
    load_template,
    open_items,
)


def rules(model: SystemModel, severity: str | None = None) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for item in open_items(model):
        if severity is None or item.severity == severity:
            out.setdefault(item.rule, []).append(item.element)
    return out


def test_complete_model_closes_every_stage() -> None:
    status = evaluate_process(complete_model())
    assert status.complete
    assert not status.human_actions
    assert not [i for i in status.items if i.severity == "blocking"]


def test_fresh_model_points_to_concept_screening_and_people() -> None:
    status = evaluate_process(bracket_model())
    states = {s.id: s.state for s in status.stages}
    assert states["requirements"] == "complete"
    assert states["concepts"] == "in_progress"
    assert states["sizing"] == "blocked"
    assert status.next_actions()[0].rule == "PR-CONCEPT"
    assert {i.element for i in status.human_actions} == {"R1", "C1"}


def test_untraced_statement_is_an_open_item() -> None:
    model = bracket_model()
    model.statements.append(Statement(id="S2", text="It should look nice."))
    assert rules(model)["PR-STATEMENT-TRACE"] == ["S2"]


def test_load_case_needs_failure_modes_and_modes_need_analysis() -> None:
    model = complete_model()
    model.load_cases.append(LoadCase(id="LC2", description="drop", requirements=["R1"]))
    model.failure_modes = []
    assert "R1" in rules(model)["PR-LOADCASE-COVERAGE"]
    model.failure_modes.append(FailureMode(id="FM1", description="crack", covers=["R1"]))
    found = rules(model)
    assert "PR-LOADCASE-COVERAGE" not in found
    assert found["PR-FM-ANALYSIS"] == ["FM1"]


def test_analysis_needs_credibility_and_a_higher_tier() -> None:
    model = complete_model()
    model.credibility = []
    assert rules(model, "blocking")["PR-CREDIBILITY"] == ["C1"]
    model.credibility.append(Credibility(id="CR1", case="C1", tier=1))
    assert rules(model, "blocking")["PR-CREDIBILITY"] == ["C1"]  # no comparison
    model.credibility[0] = Credibility(
        id="CR1", case="C1", tier=2, compared_with="C0", validated_by=["E1"]
    )
    assert "PR-CREDIBILITY" not in rules(model, "blocking")
    assert rules(model, "warning")["PR-CREDIBILITY"] == ["C1"]  # no mesh study


def test_inconsistent_models_create_a_rework_item_at_the_feedback_stage() -> None:
    model = complete_model()
    model.parts[0] = model.parts[0].model_copy(
        update={"attributes": {**model.parts[0].attributes, "mass_cad": Quantity(360.0, "g")}}
    )
    check = ConsistencyCheck(
        id="CC1",
        a=ValueRef(element="SYS", field="attributes.mass_cad"),
        b=ValueRef(element="SYS", field="attributes.mass"),
        tolerance=0.05,
        feedback_stage="sizing",
        action="Recalibrate the mass model.",
    )
    model.consistency_checks.append(check)
    items = [i for i in open_items(model) if i.rule == "PR-CONSISTENCY"]
    assert len(items) == 1 and items[0].stage == "sizing" and "20.0 %" in items[0].message
    assert evaluate_process(model).stages[3].state == "in_progress"
    model.parts[0].attributes["mass_cad"] = Quantity(305.0, "g")
    assert "PR-CONSISTENCY" not in rules(model)


def test_unreviewed_or_rejected_trade_study_blocks_the_concept_stage() -> None:
    model = bracket_model()
    add_concepts(model)
    item = next(i for i in open_items(model) if i.rule == "PR-REVIEW")
    assert item.owner == "quality_reviewer" and item.severity == "blocking"
    review(model, "TS1", findings=[{"severity": "major", "text": "K1 ignores joints"}])
    item = next(i for i in open_items(model) if i.rule == "PR-REVIEW")
    assert item.owner == "concept_engineer" and "K1 ignores joints" in item.message
    review(model, "TS1")
    assert "PR-REVIEW" not in rules(model)


def test_every_rule_owner_is_a_role() -> None:
    owners = set(STAGE_OWNER.values()) | {"quality_reviewer", "test_engineer", "human"}
    assert owners <= set(DEFAULT_ROLES)
    assert len({r.id for r in RULES}) == len(RULES)


def test_template_checks_and_yaml(tmp_path: Path) -> None:
    bad = ProcessTemplate(
        id="x",
        name="x",
        stages=[
            StageTemplate(id="a", name="a", goal="", owner="m", depends_on=["b"]),
            StageTemplate(id="b", name="b", goal="", owner="m"),
        ],
    )
    assert bad.check()
    with pytest.raises(ValueError):
        evaluate_process(bracket_model(), bad)
    path = tmp_path / "t.yaml"
    path.write_text(DEFAULT_TEMPLATE.model_dump_json())
    assert load_template(path) == DEFAULT_TEMPLATE


def test_empty_actionable_stage_asks_its_owner_to_start() -> None:
    model = bracket_model()
    add_concepts(model)
    review(model, "TS1")
    status = evaluate_process(model)
    stages = {s.id: s for s in status.stages}
    assert stages["allowables"].state == "not_started"
    assert stages["allowables"].blocking == 1
    actions = status.next_actions()
    assert [(i.rule, i.element, i.owner) for i in actions] == [
        ("PR-STAGE-CONTENT", "allowables", "materials_engineer")
    ]
    assert not [i for i in status.items if i.stage == "sizing" and i.rule == "PR-STAGE-CONTENT"]
