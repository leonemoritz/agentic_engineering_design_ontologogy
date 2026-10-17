# SPDX-License-Identifier: Apache-2.0
"""Builders shared by the model tests."""

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
from design_graph.model.elements import Attestation, Provenance

DAY = "2026-01-01"


def base_model() -> SystemModel:
    """One need, one mass requirement, one analysis case. Nothing approved, no evidence."""

    return SystemModel(
        principals=[
            Principal(
                id="eng", kind="human", name="Engineer", roles=["approver", "verifier", "witness"]
            ),
            Principal(id="bot", kind="agent", roles=["approver", "verifier"]),
        ],
        tools=[
            ToolDeclaration(id="fem", version="1", intended_use="mass estimate", declared_by="eng")
        ],
        needs=[Need(id="N1", stakeholder="customer")],
        parts=[Part(id="SYS", attributes={"mass": Quantity(2.0, "kg")})],
        requirements=[
            Requirement(
                id="R1",
                text="The system shall have a mass of at most 2.5 kg.",
                subject="SYS",
                attribute="mass",
                relation="le",
                target=Quantity(2.5, "kg"),
            )
        ],
        cases=[VerificationCase(id="C1", method="A", tool="fem")],
        relationships=[
            Relationship(id="rel1", kind="refine", source="R1", target="N1"),
            Relationship(id="rel2", kind="satisfy", source="SYS", target="R1"),
            Relationship(id="rel3", kind="verify", source="C1", target="R1"),
        ],
    )


def approve(model: SystemModel, element: str, who: str = "eng") -> None:
    model.approvals.append(
        Approval(
            id=f"APR-{element}-{len(model.approvals)}",
            element=element,
            content_hash=content_hash(model, element),
            principal=who,
            date=DAY,
        )
    )


def evidence(
    model: SystemModel,
    *,
    id: str = "E1",
    case: str = "C1",
    requirement: str = "R1",
    value: Quantity | bool | None = None,
    uncertainty: Quantity | None = None,
    date: str = DAY,
    provenance: bool = True,
    attestations: list[Attestation] | None = None,
) -> Evidence:
    item = Evidence(
        id=id,
        case=case,
        requirement=requirement,
        created_by="bot",
        date=date,
        basis_hash=basis_hash(model, requirement, case),
        value=value,
        uncertainty=uncertainty,
        provenance=Provenance(node="mass", input_hash="abc") if provenance else None,
        attestations=attestations or [],
    )
    model.evidence.append(item)
    return item


def approved_model() -> SystemModel:
    model = base_model()
    approve(model, "R1")
    approve(model, "C1")
    return model
