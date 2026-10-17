# SPDX-License-Identifier: Apache-2.0
"""Synthetic models for the process-layer and orchestration tests (invented bracket data)."""

from __future__ import annotations

from design_graph.model import (
    Approval,
    Evidence,
    Need,
    Part,
    Principal,
    Quantity,
    Relationship,
    Requirement,
    SystemModel,
    ToolDeclaration,
    VerificationCase,
    basis_hash,
    content_hash,
)
from design_graph.model.elements import Provenance
from design_graph.model.engineering import (
    Allowable,
    Concept,
    Credibility,
    Criterion,
    DesignVariable,
    FailureMode,
    LoadCase,
    Objective,
    OptimizationSpec,
    Review,
    Statement,
    Statistics,
    TradeStudy,
)

DAY = "2026-02-01"


def bracket_model() -> SystemModel:
    """Requirements stage done; nothing after it. Nothing approved."""

    return SystemModel(
        principals=[
            Principal(id="eng", kind="human", roles=["approver", "verifier", "witness"]),
            Principal(id="qa-bot", kind="agent", roles=["reviewer"]),
            Principal(id="author-bot", kind="agent", roles=["author"]),
        ],
        tools=[ToolDeclaration(id="fem", version="1", intended_use="stress", declared_by="eng")],
        statements=[Statement(id="S1", text="The bracket must hold the shelf.", source="customer")],
        needs=[Need(id="N1", stakeholder="customer")],
        parts=[
            Part(
                id="SYS",
                attributes={"capacity": Quantity(600.0, "N"), "mass": Quantity(0.30, "kg")},
            )
        ],
        requirements=[
            Requirement(
                id="R1",
                text="The bracket shall carry at least 500 N.",
                subject="SYS",
                attribute="capacity",
                relation="ge",
                target=Quantity(500.0, "N"),
            ),
        ],
        cases=[VerificationCase(id="C1", method="A", tool="fem")],
        relationships=[
            Relationship(id="t1", kind="trace", source="N1", target="S1"),
            Relationship(id="r1", kind="refine", source="R1", target="N1"),
            Relationship(id="s1", kind="satisfy", source="SYS", target="R1"),
            Relationship(id="v1", kind="verify", source="C1", target="R1"),
        ],
    )


def trade_study(selected: str = "CA", rejected: dict[str, list[str]] | None = None) -> TradeStudy:
    return TradeStudy(
        id="TS1",
        question="Which load path?",
        concepts=["CA", "CB"],
        criteria=[
            Criterion(
                id="G1",
                text="Fits the envelope",
                kind="gate",
                basis="computed",
                derived_from=["R1"],
            ),
            Criterion(id="K1", text="Mass per newton", basis="computed", derived_from=["R1"]),
            Criterion(id="K2", text="Looks robust", basis="reasoned"),
        ],
        scores={"CA": {"G1": True, "K1": 1.0}, "CB": {"G1": True, "K1": 2.0}},
        selected=[selected],
        rejected=rejected if rejected is not None else {"CB": ["K1"]},
    )


def add_concepts(model: SystemModel) -> None:
    model.concepts += [Concept(id="CA"), Concept(id="CB")]
    model.trade_studies.append(trade_study())


def review(
    model: SystemModel,
    *elements: str,
    findings: list[dict[str, str]] | None = None,
    reviewer: str = "qa-bot",
    author: str = "author-bot",
) -> None:
    from design_graph.model.engineering import ReviewFinding

    model.reviews.append(
        Review(
            id=f"REV{len(model.reviews) + 1}",
            elements=list(elements),
            reviewer=reviewer,
            author=author,
            date=DAY,
            content_hashes={e: content_hash(model, e) for e in elements},
            findings=[ReviewFinding.model_validate(f) for f in findings or []],
        )
    )


def approve(model: SystemModel, element: str) -> None:
    model.approvals.append(
        Approval(
            id=f"A-{element}",
            element=element,
            content_hash=content_hash(model, element),
            principal="eng",
            date=DAY,
        )
    )


def complete_model() -> SystemModel:
    """Every stage of the default template complete."""

    m = bracket_model()
    add_concepts(m)
    m.allowables.append(
        Allowable(
            id="AL1",
            material="PLA",
            property="tensile strength",
            value=Quantity(45.0, "MPa"),
            basis="characteristic",
            source="coupon series 1",
            status="coupon_tested",
            statistics=Statistics(mean=51.0, sd=3.0, n=10),
        )
    )
    m.load_cases.append(
        LoadCase(id="LC1", description="Shelf load at the tip", requirements=["R1"])
    )
    m.failure_modes.append(
        FailureMode(
            id="FM1", description="Bending crack at the root", covers=["R1"], analysis=["C1"]
        )
    )
    m.optimizations.append(
        OptimizationSpec(
            id="OPT1",
            subject="SYS",
            variables=[DesignVariable(name="t", lower=Quantity(2, "mm"), upper=Quantity(8, "mm"))],
            objectives=[Objective(name="mass", sense="min", attribute="mass")],
            constraints=["FM1"],
        )
    )
    m.evidence.append(
        Evidence(
            id="E1",
            case="C1",
            requirement="R1",
            created_by="author-bot",
            date=DAY,
            basis_hash=basis_hash(m, "R1", "C1"),
            value=Quantity(600.0, "N"),
            provenance=Provenance(node="capacity", input_hash="x"),
        )
    )
    m.cases.append(VerificationCase(id="C0", method="A", tool="fem", procedure="hand calculation"))
    m.credibility.append(
        Credibility(
            id="CR1",
            case="C1",
            tier=2,
            convergence_change_pct=3.0,
            compared_with="C0",
            agreement_ratio=0.95,
            validated_by=["E1"],
        )
    )
    for element in ("R1", "C1", "C0"):
        approve(m, element)
    review(m, "TS1", "AL1", "LC1", "FM1", "OPT1", "CR1")
    return m
