# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from pathlib import Path

from proc_helpers import add_concepts, bracket_model, complete_model, review, trade_study

from design_graph.model import Quantity, assess_model, dump_model, load_model, validate_model
from design_graph.model.baseline import changes_since, element_hashes
from design_graph.model.elements import Baseline
from design_graph.model.engineering import FailureMode, Review

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "model_enclosure.yaml"


def codes(model) -> set[str]:  # type: ignore[no-untyped-def]
    return {f.code for f in validate_model(model)}


def test_existing_model_and_approvals_are_unchanged() -> None:
    model = load_model(EXAMPLE)
    assert not [f for f in validate_model(model) if f.code == "APPROVAL_STALE"]
    assert assess_model(model).summary() == {"SATISFIED": 1, "NOT_SATISFIED": 1, "OPEN": 1}


def test_engineering_elements_round_trip(tmp_path: Path) -> None:
    model = complete_model()
    path = tmp_path / "model.yaml"
    dump_model(model, path)
    again = load_model(path)
    dump_model(again, tmp_path / "again.yaml")
    assert (tmp_path / "again.yaml").read_text() == path.read_text()
    assert again.failure_modes == model.failure_modes
    assert "failure_modes:" in path.read_text()


def test_failure_mode_references_and_reason() -> None:
    model = bracket_model()
    model.failure_modes.append(FailureMode(id="FM1", description="x", covers=["R9"]))
    model.failure_modes.append(
        FailureMode(id="FM2", description="y", covers=["R1"], status="unmodelled")
    )
    found = codes(model)
    assert {"ENG_DANGLING", "FM_REASON"} <= found


def test_review_must_be_independent_and_by_a_reviewer() -> None:
    model = bracket_model()
    add_concepts(model)
    review(model, "TS1", reviewer="author-bot", author="author-bot")
    found = codes(model)
    assert {"REVIEW_NOT_INDEPENDENT", "REVIEW_ROLE"} <= found


def test_review_goes_stale_when_the_element_changes() -> None:
    model = bracket_model()
    add_concepts(model)
    review(model, "TS1")
    assert "REVIEW_STALE" not in codes(model)
    model.trade_studies[0] = model.trade_studies[0].model_copy(update={"question": "Changed?"})
    assert "REVIEW_STALE" in codes(model)


def test_trade_decision_is_checked_against_the_scores() -> None:
    model = bracket_model()
    add_concepts(model)
    assert "TRADE_INCONSISTENT" not in codes(model)
    model.trade_studies[0] = trade_study(selected="CB", rejected={"CA": ["K1"]})
    assert "TRADE_INCONSISTENT" in codes(model)  # CA is better on K1, not worse
    model.trade_studies[0] = trade_study(rejected={"CB": ["G1"]})
    assert "TRADE_INCONSISTENT" in codes(model)  # CB passes the gate it is rejected for
    model.trade_studies[0] = trade_study(rejected={"CB": ["K2"]})
    found = {f.code: f.severity for f in validate_model(model)}
    assert found["TRADE_JUDGEMENT"] == "warning"


def test_review_is_never_an_approval() -> None:
    model = complete_model()
    model.approvals = []
    assert all(r.satisfaction.value == "BLOCKED" for r in assess_model(model).requirements)
    assert isinstance(model.reviews[0], Review) and model.reviews[0].accepted


def test_changed_allowable_affects_requirements_covered_by_failure_modes() -> None:
    model = complete_model()
    snapshot = Baseline(
        id="B1", created_by="eng", date="2026-02-01", element_hashes=element_hashes(model)
    )
    model.allowables[0] = model.allowables[0].model_copy(update={"value": Quantity(40.0, "MPa")})
    impact = changes_since(model, snapshot)
    assert impact.modified == ["AL1"]
    assert impact.affected_requirements == ["R1"]
