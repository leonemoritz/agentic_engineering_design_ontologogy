# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest
from helpers import DAY, approve, approved_model, base_model, evidence

from design_graph.model import (
    Assumption,
    Part,
    Quantity,
    Relationship,
    Requirement,
    RequirementAssessment,
    SatisfactionStatus,
    SystemModel,
    VerificationCase,
    VerificationStatus,
    assess_model,
)
from design_graph.model.elements import Attestation


def status(model: SystemModel, requirement: str = "R1") -> RequirementAssessment:
    return assess_model(model).by_id()[requirement]


def codes(item: RequirementAssessment) -> set[str]:
    return {reason.code for reason in item.reasons}


def test_passing_analysis_with_approvals_is_satisfied() -> None:
    model = approved_model()
    evidence(model, value=Quantity(2.0, "kg"))
    item = status(model)
    assert item.satisfaction == SatisfactionStatus.SATISFIED
    assert item.verification == VerificationStatus.VERIFIED
    assert item.margin is not None and item.margin.value == pytest.approx(0.5)


def test_failing_value_is_not_satisfied_even_when_author_claims_pass() -> None:
    model = approved_model()
    evidence(model, value=Quantity(3.0, "kg"))
    item = status(model)
    assert item.satisfaction == SatisfactionStatus.NOT_SATISFIED
    assert item.verification == VerificationStatus.FAILED


def test_uncertainty_consumes_margin() -> None:
    model = approved_model()
    evidence(model, value=Quantity(2.4, "kg"), uncertainty=Quantity(0.2, "kg"))
    assert status(model).satisfaction == SatisfactionStatus.NOT_SATISFIED


def test_units_are_converted_before_comparison() -> None:
    model = approved_model()
    evidence(model, value=Quantity(2400.0, "g"))
    assert status(model).satisfaction == SatisfactionStatus.SATISFIED


def test_no_evidence_is_open_not_satisfied() -> None:
    item = status(approved_model())
    assert item.satisfaction == SatisfactionStatus.OPEN
    assert item.verification == VerificationStatus.UNVERIFIED


def test_unapproved_requirement_is_blocked_even_with_passing_evidence() -> None:
    model = base_model()
    approve(model, "C1")
    evidence(model, value=Quantity(2.0, "kg"))
    item = status(model)
    assert item.satisfaction == SatisfactionStatus.BLOCKED
    assert "REQ_NOT_APPROVED" in codes(item)


def test_agent_approval_does_not_count() -> None:
    model = base_model()
    approve(model, "R1", who="bot")
    approve(model, "C1", who="bot")
    evidence(model, value=Quantity(2.0, "kg"))
    assert status(model).satisfaction == SatisfactionStatus.BLOCKED


def test_unapproved_case_does_not_verify() -> None:
    model = base_model()
    approve(model, "R1")
    evidence(model, value=Quantity(2.0, "kg"))
    item = status(model)
    assert "CASE_NOT_APPROVED" in codes(item)
    assert item.satisfaction != SatisfactionStatus.SATISFIED


def test_editing_requirement_invalidates_approval_and_evidence() -> None:
    model = approved_model()
    evidence(model, value=Quantity(2.0, "kg"))
    model.requirements[0] = model.requirements[0].model_copy(update={"target": Quantity(3.0, "kg")})
    item = status(model)
    assert item.satisfaction == SatisfactionStatus.BLOCKED
    assert item.verification == VerificationStatus.STALE
    assert {"REQ_NOT_APPROVED", "EVIDENCE_STALE"} <= codes(item)


def test_changing_subject_part_makes_evidence_stale() -> None:
    model = approved_model()
    evidence(model, value=Quantity(2.0, "kg"))
    model.parts[0] = Part(id="SYS", attributes={"mass": Quantity(2.2, "kg")})
    assert status(model).verification == VerificationStatus.STALE


def test_reapproval_after_edit_still_needs_new_evidence() -> None:
    model = approved_model()
    evidence(model, value=Quantity(2.0, "kg"))
    model.requirements[0] = model.requirements[0].model_copy(update={"tbc": False, "name": "x"})
    approve(model, "R1")
    item = status(model)
    assert item.verification == VerificationStatus.STALE
    assert item.satisfaction == SatisfactionStatus.OPEN


def test_latest_current_evidence_decides() -> None:
    model = approved_model()
    evidence(model, id="E1", value=Quantity(3.0, "kg"), date="2026-01-01")
    evidence(model, id="E2", value=Quantity(2.0, "kg"), date="2026-02-01")
    assert status(model).satisfaction == SatisfactionStatus.SATISFIED
    evidence(model, id="E3", value=Quantity(3.1, "kg"), date="2026-03-01")
    assert status(model).satisfaction == SatisfactionStatus.NOT_SATISFIED


def test_analysis_without_provenance_is_incomplete() -> None:
    model = approved_model()
    evidence(model, value=Quantity(2.0, "kg"), provenance=False)
    item = status(model)
    assert item.satisfaction == SatisfactionStatus.OPEN
    assert "EVIDENCE_NO_PROVENANCE" in codes(item)


def test_dimension_mismatch_evidence_is_never_a_pass() -> None:
    model = approved_model()
    evidence(model, value=Quantity(1.0, "m"))
    item = status(model)
    assert item.satisfaction != SatisfactionStatus.SATISFIED
    assert "EVIDENCE_INVALID" in codes(item)


def test_offset_temperature_units_compare_in_kelvin() -> None:
    model = approved_model()
    model.requirements[0] = model.requirements[0].model_copy(
        update={"target": Quantity(350.0, "K")}
    )
    model.parts[0] = Part(id="SYS", attributes={"mass": Quantity(300.0, "K")})
    approve(model, "R1")
    approve(model, "C1")
    evidence(model, value=Quantity(70.0, "degC"))  # 343.15 K <= 350 K
    assert status(model).satisfaction == SatisfactionStatus.SATISFIED


def test_open_assumption_blocks_satisfaction_until_closed() -> None:
    model = approved_model()
    evidence(model, value=Quantity(2.0, "kg"))
    model.assumptions.append(
        Assumption(id="A1", statement="density is nominal", owner="eng", impacts=["R1"])
    )
    assert "ASSUMPTION_OPEN" in codes(status(model))
    assert status(model).satisfaction == SatisfactionStatus.BLOCKED
    model.assumptions[0] = model.assumptions[0].model_copy(
        update={"status": "closed", "closure": "measured"}
    )
    assert status(model).satisfaction == SatisfactionStatus.SATISFIED


def test_tbc_requirement_is_blocked() -> None:
    model = base_model()
    model.requirements[0] = model.requirements[0].model_copy(update={"tbc": True})
    approve(model, "R1")
    approve(model, "C1")
    evidence(model, value=Quantity(2.0, "kg"))
    assert "REQ_TBC" in codes(status(model))


def test_missing_design_claim_blocks() -> None:
    model = approved_model()
    evidence(model, value=Quantity(2.0, "kg"))
    model.relationships = [r for r in model.relationships if r.kind != "satisfy"]
    approve(model, "C1")
    assert "NO_DESIGN_CLAIM" in codes(status(model))


def test_test_method_needs_witness_attestation() -> None:
    model = base_model()
    model.cases[0] = VerificationCase(id="C1", method="T", tool="fem")
    approve(model, "R1")
    approve(model, "C1")
    evidence(model, value=Quantity(2.0, "kg"))
    assert "ATTEST_MISSING" in codes(status(model))
    model.evidence.clear()
    evidence(
        model,
        value=Quantity(2.0, "kg"),
        attestations=[Attestation(principal="eng", role="witness", outcome="pass", date=DAY)],
    )
    assert status(model).satisfaction == SatisfactionStatus.SATISFIED


def test_agent_attestation_is_ignored() -> None:
    model = base_model()
    model.cases[0] = VerificationCase(id="C1", method="I", tool=None)
    approve(model, "R1")
    approve(model, "C1")
    evidence(
        model,
        value=None,
        attestations=[Attestation(principal="bot", role="verifier", outcome="pass", date=DAY)],
    )
    item = status(model)
    assert item.satisfaction == SatisfactionStatus.OPEN
    assert {"ATTEST_UNAUTHORIZED", "ATTEST_MISSING"} <= codes(item)


def test_failed_attestation_fails_even_if_value_passes() -> None:
    model = base_model()
    model.cases[0] = VerificationCase(id="C1", method="I")
    approve(model, "R1")
    approve(model, "C1")
    evidence(
        model,
        value=Quantity(2.0, "kg"),
        attestations=[Attestation(principal="eng", role="verifier", outcome="fail", date=DAY)],
    )
    assert status(model).satisfaction == SatisfactionStatus.NOT_SATISFIED


def test_all_cases_must_pass() -> None:
    model = approved_model()
    model.cases.append(VerificationCase(id="C2", method="A", tool="fem"))
    model.relationships.append(Relationship(id="rel4", kind="verify", source="C2", target="R1"))
    approve(model, "C1")  # verify set unchanged for C1, so existing approval stays valid
    approve(model, "C2")
    evidence(model, value=Quantity(2.0, "kg"))
    item = status(model)
    assert item.verification == VerificationStatus.INCOMPLETE
    evidence(model, id="E2", case="C2", value=Quantity(2.0, "kg"))
    assert status(model).satisfaction == SatisfactionStatus.SATISFIED


def test_failed_child_fails_parent_and_open_child_keeps_parent_open() -> None:
    model = approved_model()
    model.requirements.append(
        Requirement(
            id="R2",
            text="The housing shall have a mass of at most 1 kg.",
            subject="SYS",
            attribute="mass",
            relation="le",
            target=Quantity(1.0, "kg"),
        )
    )
    model.cases.append(VerificationCase(id="C2", method="A", tool="fem"))
    model.relationships += [
        Relationship(id="rel4", kind="derive", source="R2", target="R1"),
        Relationship(id="rel5", kind="satisfy", source="SYS", target="R2"),
        Relationship(id="rel6", kind="verify", source="C2", target="R2"),
    ]
    approve(model, "R2")
    approve(model, "C2")
    approve(model, "C1")
    evidence(model, value=Quantity(2.0, "kg"))
    parent = status(model)
    assert parent.satisfaction == SatisfactionStatus.OPEN
    assert "CHILD_NOT_SATISFIED" in codes(parent)
    evidence(model, id="E2", case="C2", requirement="R2", value=Quantity(1.5, "kg"))
    assert status(model).satisfaction == SatisfactionStatus.NOT_SATISFIED


def test_withdrawn_requirement_is_reported_withdrawn() -> None:
    model = approved_model()
    model.requirements[0] = model.requirements[0].model_copy(update={"withdrawn": True})
    assert status(model).satisfaction == SatisfactionStatus.WITHDRAWN


def test_bool_requirement() -> None:
    model = base_model()
    model.requirements[0] = Requirement(
        id="R1",
        text="The system shall be sealed.",
        subject="SYS",
        attribute="sealed",
        relation="bool",
    )
    approve(model, "R1")
    approve(model, "C1")
    evidence(model, value=True)
    assert status(model).satisfaction == SatisfactionStatus.SATISFIED
    evidence(model, id="E2", value=False, date="2026-06-01")
    assert status(model).satisfaction == SatisfactionStatus.NOT_SATISFIED
