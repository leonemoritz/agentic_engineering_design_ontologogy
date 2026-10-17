# SPDX-License-Identifier: Apache-2.0
"""Baselines with change impact, and construction of evidence from graph results."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from design_graph.core import Graph
from design_graph.model.elements import Baseline, Evidence, Provenance, digest
from design_graph.model.system import SystemModel, basis_hash, content_hash
from design_graph.model.units import Quantity
from design_graph.model.validate import validate_model

_BASELINED = (
    "needs",
    "parts",
    "requirements",
    "cases",
    "assumptions",
    "tools",
    # design-defining engineering elements
    "design_rules",
    "trade_studies",
    "load_cases",
    "failure_modes",
    "optimizations",
    "allowables",
)


class Impact(BaseModel):
    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    modified: list[str] = Field(default_factory=list)
    affected_requirements: list[str] = Field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not (self.added or self.removed or self.modified)


def element_hashes(model: SystemModel) -> dict[str, str]:
    hashes = {
        item.id: content_hash(model, item.id)
        for name in _BASELINED
        for item in getattr(model, name)
    }
    for rel in model.relationships:
        hashes[rel.id] = digest(rel.model_dump(mode="json"))
    return hashes


def create_baseline(
    model: SystemModel, *, id: str, created_by: str, date: str, note: str = ""
) -> Baseline:
    """Snapshot the model. Refuses a model with errors or a non-human, non-approver creator."""

    errors = [f for f in validate_model(model) if f.severity == "error"]
    if errors:
        raise ValueError(f"model has {len(errors)} validation errors; first: {errors[0].message}")
    who = model.principal(created_by)
    if who is None or who.kind != "human" or "approver" not in who.roles:
        raise ValueError("baselines must be created by a human approver")
    return Baseline(
        id=id, created_by=created_by, date=date, element_hashes=element_hashes(model), note=note
    )


def changes_since(model: SystemModel, baseline: Baseline) -> Impact:
    now = element_hashes(model)
    then = baseline.element_hashes
    added = sorted(set(now) - set(then))
    removed = sorted(set(then) - set(now))
    modified = sorted(k for k in set(now) & set(then) if now[k] != then[k])
    return Impact(
        added=added,
        removed=removed,
        modified=modified,
        affected_requirements=_affected(model, set(added) | set(removed) | set(modified)),
    )


def _affected(model: SystemModel, changed: set[str]) -> list[str]:
    seeds: set[str] = set()
    requirement_ids = {req.id for req in model.requirements}
    for item in changed:
        if item in requirement_ids:
            seeds.add(item)
        for rel in model.relationships:
            if item in (rel.id, rel.source, rel.target):
                seeds.update({rel.source, rel.target} & requirement_ids)
                if rel.kind == "verify":
                    seeds.update(model.related("verify", source=rel.source))
    for req in model.requirements:
        if req.subject in changed:
            seeds.add(req.id)
    for case in model.cases:
        if case.id in changed or (case.tool is not None and case.tool in changed):
            seeds.update(model.related("verify", source=case.id))
    for mode in model.failure_modes:
        if mode.id in changed or any(case in changed for case in mode.analysis):
            seeds.update(set(mode.covers) & requirement_ids)
    for load_case in model.load_cases:
        if load_case.id in changed:
            seeds.update(set(load_case.requirements) & requirement_ids)
    if any(a.id in changed for a in model.allowables):
        for mode in model.failure_modes:
            if mode.status == "modelled":
                seeds.update(set(mode.covers) & requirement_ids)
    affected = set(seeds)
    stack = list(seeds)
    while stack:
        for parent in model.related("derive", source=stack.pop()):
            if parent not in affected:
                affected.add(parent)
                stack.append(parent)
    return sorted(affected)


def evidence_from_graph(
    model: SystemModel,
    graph: Graph,
    *,
    id: str,
    case: str,
    requirement: str,
    created_by: str,
    date: str,
    node: str,
    unit: str,
    path: str = "",
    uncertainty: Quantity | None = None,
) -> Evidence:
    """Build analysis evidence from an evaluated graph node, bound to the current model basis."""

    value: Any = graph.evaluate(node)[node]
    for part in path.split(".") if path else []:
        value = value[part] if isinstance(value, dict) else getattr(value, part)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{node}.{path} did not resolve to a number")
    record = graph.provenance(node)
    return Evidence(
        id=id,
        case=case,
        requirement=requirement,
        created_by=created_by,
        date=date,
        basis_hash=basis_hash(model, requirement, case),
        value=Quantity(float(value), unit),
        uncertainty=uncertainty,
        provenance=Provenance(node=node, input_hash=record.input_hash, function=record.function),
    )
