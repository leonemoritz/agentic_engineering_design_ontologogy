# SPDX-License-Identifier: Apache-2.0
"""Engineering elements for the system model: the work between requirements and evidence.

The core model records needs, requirements, cases and evidence. These elements record how an
engineer (or an agent) gets from one to the other: the raw statements requirements come from, design
rules, concepts and trade studies, load cases, failure modes, the optimisation spec, material
allowables, model credibility, independent reviews and cross-model consistency checks. They are
optional; a model without them stays valid. The process layer (`design_graph.process`) uses them to
say what is missing and what to do next.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from design_graph.model.elements import ElementId, IsoDate, _Element
from design_graph.model.units import Quantity


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Statement(_Element):
    """Raw stakeholder text, kept verbatim so every need and requirement can be traced to what was
    said.
    """

    text: str
    source: str = ""
    date: IsoDate | None = None


class DesignRule(_Element):
    """A practice adopted by the designer. Not a requirement: it constrains the solution, not the
    need.
    """

    text: str
    derived_from: list[ElementId] = Field(default_factory=list)
    status: Literal["unverified", "verified"] = "unverified"


class Concept(_Element):
    """A candidate solution considered in a trade study."""

    description: str = ""


class Criterion(_Strict):
    """A gate (pass/fail) or a criterion (ranked) in a trade study, traced to what it comes from."""

    id: ElementId
    text: str
    kind: Literal["gate", "criterion"] = "criterion"
    basis: Literal["computed", "reasoned"]
    better: Literal["lower", "higher"] = "lower"
    derived_from: list[ElementId] = Field(default_factory=list)


class TradeStudy(_Element):
    """Concepts screened against gates and criteria, with a decision that can be checked against the
    scores.
    """

    question: str
    concepts: list[ElementId]
    criteria: list[Criterion]
    scores: dict[str, dict[str, float | bool]] = Field(default_factory=dict)
    selected: list[ElementId] = Field(default_factory=list)
    rejected: dict[str, list[str]] = Field(default_factory=dict)
    decided_by: ElementId | None = None


class LoadCase(_Element):
    """A defined loading for performance requirements; the requirements it realises must be covered
    by failure modes.
    """

    description: str
    requirements: list[ElementId]
    assumptions: list[ElementId] = Field(default_factory=list)
    loads: dict[str, Quantity] = Field(default_factory=dict)


class FailureMode(_Element):
    """A way the design can fail, the requirements it threatens, and the analysis that covers it."""

    description: str
    covers: list[ElementId]
    status: Literal["modelled", "unmodelled"] = "modelled"
    analysis: list[ElementId] = Field(default_factory=list)
    validity: str = ""
    reason: str = ""


class DesignVariable(_Strict):
    name: str
    lower: Quantity
    upper: Quantity
    value: Quantity | None = None


class Objective(_Strict):
    name: str
    sense: Literal["min", "max"]
    attribute: str


class OptimizationSpec(_Element):
    """Variables, objectives and constraints of a sizing problem, so the search is declared, not
    implicit.
    """

    subject: ElementId
    variables: list[DesignVariable]
    objectives: list[Objective]
    constraints: list[ElementId] = Field(default_factory=list)
    method: str = ""


class Statistics(_Strict):
    mean: float
    sd: float | None = None
    n: int | None = None


class Allowable(_Element):
    """A material value an analysis may use, with its basis, source and test status."""

    material: str
    property: str
    value: Quantity
    basis: Literal["mean", "characteristic", "design"]
    source: str
    status: Literal["datasheet", "assumed", "coupon_tested"]
    statistics: Statistics | None = None
    knockdowns: dict[str, float] = Field(default_factory=dict)


class Credibility(_Element):
    """How far a verification case's results can be trusted: tier, convergence, comparison,
    calibration.
    """

    case: ElementId
    tier: int = Field(ge=1, le=3)
    convergence_change_pct: float | None = None
    compared_with: ElementId | None = None
    agreement_ratio: float | None = None
    calibration: dict[str, float] = Field(default_factory=dict)
    validated_by: list[ElementId] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    waiver: str = ""


class ReviewFinding(_Strict):
    severity: Literal["blocking", "major", "minor", "note"]
    text: str
    element: ElementId | None = None
    resolved: bool = False


class Review(_Element):
    """An independent review of elements, pinned to their content. Agents may review; a review is
    never an approval and never satisfies a requirement.
    """

    elements: list[ElementId]
    reviewer: ElementId
    author: ElementId
    content_hashes: dict[str, str] = Field(default_factory=dict)
    date: IsoDate
    checked: list[str] = Field(default_factory=list)
    findings: list[ReviewFinding] = Field(default_factory=list)

    @property
    def accepted(self) -> bool:
        return not any(
            f.severity in {"blocking", "major"} and not f.resolved for f in self.findings
        )


class ValueRef(_Strict):
    """A number in the model: a part attribute (`attributes.<name>`), an evidence value (`value`),
    or a numeric field of any element (for example `agreement_ratio` on a Credibility record).
    """

    element: ElementId
    field: str


class ConsistencyCheck(_Element):
    """Two values from independent models that must agree within a relative tolerance. A violation
    becomes a rework item at `feedback_stage`.
    """

    a: ValueRef
    b: ValueRef
    tolerance: float = Field(gt=0)
    feedback_stage: str
    action: str = ""


ENGINEERING_COLLECTIONS: dict[str, type[_Element]] = {
    "statements": Statement,
    "design_rules": DesignRule,
    "concepts": Concept,
    "trade_studies": TradeStudy,
    "load_cases": LoadCase,
    "failure_modes": FailureMode,
    "optimizations": OptimizationSpec,
    "allowables": Allowable,
    "credibility": Credibility,
    "reviews": Review,
    "consistency_checks": ConsistencyCheck,
}
