# SPDX-License-Identifier: Apache-2.0
"""Process templates: the order of engineering work, what each stage produces, when it is done."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


class StageTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    goal: str
    owner: str
    depends_on: list[str] = Field(default_factory=list)
    requires: list[str] = Field(default_factory=list)
    exit_criterion: str = ""


class ProcessTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    stages: list[StageTemplate]

    def stage(self, stage_id: str) -> StageTemplate | None:
        return next((s for s in self.stages if s.id == stage_id), None)

    def order(self, stage_id: str) -> int:
        ids = [s.id for s in self.stages]
        return ids.index(stage_id) if stage_id in ids else len(ids)

    def check(self) -> list[str]:
        """Return problems with the template itself (unknown dependencies, cycles)."""

        ids = {s.id for s in self.stages}
        problems = [
            f"{s.id} depends on unknown stage {d}"
            for s in self.stages
            for d in s.depends_on
            if d not in ids
        ]
        seen: list[str] = []
        for s in self.stages:
            for d in s.depends_on:
                if d in ids and d not in seen:
                    problems.append(f"{s.id} depends on {d}, which comes later")
            seen.append(s.id)
        return problems


def _stage(
    id: str,
    name: str,
    owner: str,
    goal: str,
    exit_criterion: str,
    *,
    depends_on: list[str],
    requires: list[str],
) -> StageTemplate:
    return StageTemplate(
        id=id,
        name=name,
        owner=owner,
        goal=goal,
        exit_criterion=exit_criterion,
        depends_on=depends_on,
        requires=requires,
    )


DEFAULT_TEMPLATE = ProcessTemplate(
    id="engineering-v",
    name="Requirements-first engineering design with tiered verification",
    stages=[
        _stage(
            "requirements",
            "Requirements",
            "requirements_engineer",
            "Interpret every stakeholder statement into needs, solution-neutral requirements, "
            "design rules and explicit assumptions.",
            "Every statement traced; every requirement verifiable and traced to a need; every "
            "open assumption has a closure plan.",
            depends_on=[],
            requires=["needs", "requirements"],
        ),
        _stage(
            "concepts",
            "Concept screening",
            "concept_engineer",
            "Screen alternative concepts against gates and criteria derived from the requirements.",
            "A trade study selects a concept; rejections cite computed criteria; the study is "
            "independently reviewed.",
            depends_on=["requirements"],
            requires=["concepts", "trade_studies"],
        ),
        _stage(
            "allowables",
            "Allowables",
            "materials_engineer",
            "Record the material values the analysis may use, with source, basis and statistics.",
            "Every value sourced or marked assumed; a coupon plan for untested values.",
            depends_on=["concepts"],
            requires=["allowables"],
        ),
        _stage(
            "sizing",
            "Load cases, failure modes and sizing",
            "structures_analyst",
            "Define load cases, list every failure mode, and size the design against all of them.",
            "Every load-case requirement covered by failure modes; every modelled mode has an "
            "analysis; the sizing problem is declared.",
            depends_on=["concepts", "allowables"],
            requires=["load_cases", "failure_modes", "optimizations"],
        ),
        _stage(
            "analysis",
            "Tiered analysis and credibility",
            "simulation_credibility",
            "Check tier-1 results with a higher tier (FEM, CAD), converge, compare and calibrate.",
            "Every analysis has a credibility record, a higher-tier comparison and a converged "
            "mesh; independent models agree within tolerance.",
            depends_on=["sizing"],
            requires=["evidence", "credibility"],
        ),
        _stage(
            "verification",
            "Verification and sign-off",
            "structures_analyst",
            "Current, passing evidence for every case; independent reviews; human approvals.",
            "Every requirement verified, reviewed, and approved by a human.",
            depends_on=["analysis"],
            requires=["evidence"],
        ),
        _stage(
            "test",
            "Test",
            "test_engineer",
            "Confirm allowables and performance by test, witnessed by a person.",
            "Coupon and system tests attested; allowables replaced by measured values.",
            depends_on=["verification"],
            requires=[],
        ),
    ],
)


def load_template(path: str | Path) -> ProcessTemplate:
    return ProcessTemplate.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
