# SPDX-License-Identifier: Apache-2.0
"""Typed MBSE model elements, relationships, evidence, approvals and baselines."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
)

from design_graph.model.units import Quantity

ElementId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:\-]*$")]
Method = Literal["A", "R", "I", "T"]
Relation = Literal["le", "ge", "eq", "range", "bool"]
RelationshipKind = Literal["derive", "refine", "satisfy", "verify", "allocate", "trace"]
# approver, verifier, witness: human only (enforced by validation and assessment).
# author, reviewer: may be held by agents; a review is never an approval.
Role = Literal["approver", "verifier", "witness", "author", "reviewer"]


def _check_date(value: str) -> str:
    try:
        datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{value!r} is not an ISO 8601 date") from error
    return value


IsoDate = Annotated[str, AfterValidator(_check_date)]


class _Element(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: ElementId
    name: str = ""
    doc: str = ""
    withdrawn: bool = False


class Principal(_Element):
    """A person or agent. Only human principals may approve or attest."""

    kind: Literal["human", "agent"]
    roles: list[Role] = Field(default_factory=list)


class ToolDeclaration(_Element):
    """Toolchain preselected by the engineer; the framework records it, not assesses it."""

    version: str
    intended_use: str
    declared_by: ElementId


class Need(_Element):
    stakeholder: str
    rationale: str = ""


class Part(_Element):
    parent: ElementId | None = None
    attributes: dict[str, Quantity] = Field(default_factory=dict)


class Requirement(_Element):
    """A statement on a subject. `relation=None` makes it qualitative (attestation only)."""

    text: str
    subject: ElementId
    attribute: str | None = None
    relation: Relation | None = None
    target: Quantity | None = None
    upper: Quantity | None = None
    tolerance: Quantity | None = None
    expected: bool = True
    tbc: bool = False


class VerificationCase(_Element):
    method: Method
    level: str = ""
    stage: str = ""
    procedure: str = ""
    tool: ElementId | None = None


class Assumption(_Element):
    statement: str
    owner: ElementId
    status: Literal["open", "closed"] = "open"
    impacts: list[ElementId] = Field(default_factory=list)
    closure: str = ""


class Relationship(_Element):
    kind: RelationshipKind
    source: ElementId
    target: ElementId
    rationale: str = ""


class Provenance(BaseModel):
    """Graph run that produced an analysis value."""

    model_config = ConfigDict(extra="forbid")

    node: str
    input_hash: str
    function: str = ""


class Attestation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    principal: ElementId
    role: Literal["verifier", "witness"]
    outcome: Literal["pass", "fail"]
    date: IsoDate
    note: str = ""


class Evidence(_Element):
    """One result for one (case, requirement) pair. Pass/fail is computed, never stated."""

    case: ElementId
    requirement: ElementId
    created_by: ElementId
    date: IsoDate
    basis_hash: str
    value: Quantity | StrictBool | None = None
    uncertainty: Quantity | None = None
    provenance: Provenance | None = None
    attestations: list[Attestation] = Field(default_factory=list)


class Approval(_Element):
    """A human approval pinned to the element content hash at approval time."""

    element: ElementId
    content_hash: str
    principal: ElementId
    date: IsoDate
    note: str = ""


class Baseline(_Element):
    created_by: ElementId
    date: IsoDate
    element_hashes: dict[str, str]
    note: str = ""


def digest(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
