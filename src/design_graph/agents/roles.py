# SPDX-License-Identifier: Apache-2.0
"""Agent roles: what each role works on, which model collections it may write, and its checklist.

The checklists are the guidance. They carry the questions an experienced engineer asks at each
stage, so an agent in that role is prompted to ask them too.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RoleKind = Literal["manager", "expert", "quality", "human"]

# Collections an agent may never write, whatever its role: approvals, baselines, identities and the
# toolchain declaration remain human decisions.
FORBIDDEN_FOR_AGENTS = frozenset({"approvals", "baselines", "principals", "tools"})


class RoleSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    kind: RoleKind
    stages: list[str] = Field(default_factory=list)
    writes: list[str] = Field(default_factory=list)
    checklist: list[str] = Field(default_factory=list)


def _role(
    id: str, title: str, kind: RoleKind, stages: list[str], writes: list[str], checklist: list[str]
) -> RoleSpec:
    return RoleSpec(
        id=id, title=title, kind=kind, stages=stages, writes=writes, checklist=checklist
    )


DEFAULT_ROLES: dict[str, RoleSpec] = {
    r.id: r
    for r in [
        _role(
            "manager",
            "Engineering manager (orchestrator)",
            "manager",
            [],
            [],
            [
                "Work the earliest unfinished stage first; never start a stage whose inputs are "
                "incomplete.",
                "Route each open item to the role that owns it; route approvals and tests to a "
                "person.",
                "Send every accepted change to an independent reviewer.",
            ],
        ),
        _role(
            "requirements_engineer",
            "Requirements engineer",
            "expert",
            ["requirements"],
            [
                "statements",
                "needs",
                "requirements",
                "relationships",
                "assumptions",
                "design_rules",
                "parts",
            ],
            [
                "Interpret each stakeholder statement separately and trace it.",
                "Keep requirements solution-neutral; put solution choices in design rules.",
                "Give every requirement a verification method and a parent need.",
                "Derive what the statements leave implicit: load case, failure definition, "
                "environment, duration, interfaces, safety.",
                "State every assumption with an owner and a closure plan.",
            ],
        ),
        _role(
            "concept_engineer",
            "Concept engineer",
            "expert",
            ["concepts"],
            ["concepts", "trade_studies", "design_rules"],
            [
                "List at least three genuinely different concepts, including one you expect to "
                "lose.",
                "Derive gates and criteria from requirements; mark each computed or reasoned.",
                "Compute the criteria; a rejection that rests on judgement only must say so.",
                "Check that the decision is consistent with the scores.",
            ],
        ),
        _role(
            "materials_engineer",
            "Materials engineer",
            "expert",
            ["allowables"],
            ["allowables", "assumptions"],
            [
                "Cite a primary source (datasheet, standard, test) for every value.",
                "State the basis: mean, characteristic or design; record statistics.",
                "Apply and record knockdowns for process, orientation, environment and duration.",
                "Plan coupon tests for every value that is not tested.",
            ],
        ),
        _role(
            "structures_analyst",
            "Structures analyst",
            "expert",
            ["sizing", "verification"],
            [
                "load_cases",
                "failure_modes",
                "optimizations",
                "parts",
                "cases",
                "evidence",
                "relationships",
            ],
            [
                "Define each load case: magnitude, location, contact area, supports, duration.",
                "List every failure mode: material strength, stability (member and global), "
                "joints, supports and bearing, interfaces, manufacturing defects. Mark the ones "
                "not modelled.",
                "Check the analysis against hand statics before trusting it.",
                "Declare the sizing problem; add manufacturability floors so an optimiser cannot "
                "exploit gaps in the model.",
                "Make sure the analysed geometry is the geometry that can be built.",
            ],
        ),
        _role(
            "cad_dfm_engineer",
            "CAD and design-for-manufacture engineer",
            "expert",
            ["analysis"],
            ["parts", "design_rules", "evidence", "consistency_checks"],
            [
                "Check that every part can be made and assembled: clearances, features, "
                "orientation.",
                "Check every joint transmits the forces the analysis assumes.",
                "Compare CAD mass and dimensions with the sizing model.",
            ],
        ),
        _role(
            "simulation_credibility",
            "Simulation credibility engineer",
            "expert",
            ["analysis"],
            ["credibility", "cases", "evidence", "consistency_checks"],
            [
                "Run at least two mesh sizes; record the change in the governing result.",
                "Exclude singularities at loads and supports; say how.",
                "Compare with the lower tier; record the agreement ratio and any calibration "
                "factor.",
                "State the limitations and the validity range.",
            ],
        ),
        _role(
            "test_engineer",
            "Test engineer",
            "expert",
            ["test"],
            ["cases", "assumptions"],
            [
                "Plan coupon tests for allowables and system tests for performance.",
                "Specify specimens, count, procedure, measurements and pass criteria.",
                "Tests are performed and witnessed by people; plan, do not attest.",
            ],
        ),
        _role(
            "quality_reviewer",
            "Independent quality reviewer",
            "quality",
            [],
            ["reviews"],
            [
                "Review only the elements, not the author's reasoning; try to show they are wrong.",
                "Check every element against the requirements it claims to serve.",
                "Recompute at least one number per element where possible.",
                "Classify findings as blocking, major, minor or note; never approve.",
            ],
        ),
        _role(
            "human",
            "Responsible engineer (person)",
            "human",
            [],
            [],
            ["Declare the toolchain, approve requirements and cases, perform and witness tests."],
        ),
    ]
}
