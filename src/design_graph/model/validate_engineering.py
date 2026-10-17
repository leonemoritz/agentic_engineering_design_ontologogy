# SPDX-License-Identifier: Apache-2.0
"""Validation rules for the engineering elements (design_graph.model.engineering)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel

from design_graph.model.elements import (
    Evidence,
    Need,
    Part,
    Principal,
    Requirement,
    VerificationCase,
)
from design_graph.model.engineering import (
    Concept,
    DesignRule,
    FailureMode,
    TradeStudy,
    ValueRef,
)
from design_graph.model.system import SystemModel, content_hash
from design_graph.model.units import Quantity, same_dimension

Severity = Literal["error", "warning"]
_Add = Callable[[str, Severity, str, str], None]


def check_engineering(model: SystemModel, add: _Add) -> None:
    elements = model.elements()

    def expect(owner: str, ref: str, types: tuple[type, ...], what: str) -> None:
        item = elements.get(ref)
        if item is None:
            add("ENG_DANGLING", "error", owner, f"references unknown {what} {ref!r}")
        elif not isinstance(item, types):
            add("ENG_TYPE", "error", owner, f"{ref!r} is a {type(item).__name__}, not a {what}")

    for rule in model.design_rules:
        for ref in rule.derived_from:
            expect(rule.id, ref, (Requirement, Need, DesignRule), "requirement or need")
    for study in model.trade_studies:
        _check_trade_study(model, study, add, expect)
    for load_case in model.load_cases:
        for ref in load_case.requirements:
            expect(load_case.id, ref, (Requirement,), "requirement")
        for ref in load_case.assumptions:
            if ref not in {a.id for a in model.assumptions}:
                add("ENG_DANGLING", "error", load_case.id, f"references unknown assumption {ref!r}")
    for mode in model.failure_modes:
        if not mode.covers:
            add("FM_NO_REQUIREMENT", "error", mode.id, "failure mode covers no requirement")
        for ref in mode.covers:
            expect(mode.id, ref, (Requirement,), "requirement")
        for ref in mode.analysis:
            expect(mode.id, ref, (VerificationCase,), "verification case")
        if mode.status == "unmodelled" and not mode.reason.strip():
            add(
                "FM_REASON",
                "error",
                mode.id,
                "unmodelled failure mode needs a reason and mitigation",
            )
    for spec in model.optimizations:
        expect(spec.id, spec.subject, (Part,), "part")
        for ref in spec.constraints:
            expect(spec.id, ref, (FailureMode, Requirement), "failure mode or requirement")
        for var in spec.variables:
            if not same_dimension(var.lower, var.upper):
                add(
                    "OPT_DIMENSION",
                    "error",
                    spec.id,
                    f"variable {var.name}: bounds differ in dimension",
                )
            elif var.upper.to_base() < var.lower.to_base():
                add("OPT_BOUNDS", "error", spec.id, f"variable {var.name}: upper below lower")
    for allowable in model.allowables:
        if allowable.status != "assumed" and not allowable.source.strip():
            add("ALLOWABLE_SOURCE", "error", allowable.id, "allowable has no source")
        if (
            allowable.statistics
            and allowable.statistics.sd is not None
            and allowable.statistics.sd < 0
        ):
            add("ALLOWABLE_STATS", "error", allowable.id, "standard deviation is negative")
    for record in model.credibility:
        expect(record.id, record.case, (VerificationCase,), "verification case")
        if record.compared_with is not None:
            expect(record.id, record.compared_with, (VerificationCase,), "verification case")
        for ref in record.validated_by:
            expect(record.id, ref, (Evidence,), "evidence")
    for review in model.reviews:
        for ref in review.elements:
            if ref not in elements:
                add("ENG_DANGLING", "error", review.id, f"reviews unknown element {ref!r}")
            elif ref in review.content_hashes and review.content_hashes[ref] != content_hash(
                model, ref
            ):
                add("REVIEW_STALE", "warning", review.id, f"{ref} changed since it was reviewed")
        reviewer = model.principal(review.reviewer)
        if reviewer is None:
            add("ENG_DANGLING", "error", review.id, f"unknown reviewer {review.reviewer!r}")
        elif "reviewer" not in reviewer.roles:
            add("REVIEW_ROLE", "error", review.id, f"{reviewer.id} does not hold the reviewer role")
        if model.principal(review.author) is None:
            add("ENG_DANGLING", "error", review.id, f"unknown author {review.author!r}")
        if review.reviewer == review.author:
            add(
                "REVIEW_NOT_INDEPENDENT",
                "error",
                review.id,
                "reviewer and author are the same principal",
            )
    for check in model.consistency_checks:
        for value_ref in (check.a, check.b):
            if resolve_value(model, value_ref) is None:
                add(
                    "CONSISTENCY_REF",
                    "warning",
                    check.id,
                    f"cannot resolve {value_ref.element}.{value_ref.field}",
                )


def _check_trade_study(
    model: SystemModel,
    study: TradeStudy,
    add: _Add,
    expect: Callable[[str, str, tuple[type, ...], str], None],
) -> None:
    for ref in study.concepts:
        expect(study.id, ref, (Concept,), "concept")
    criteria = {c.id: c for c in study.criteria}
    for crit in study.criteria:
        for ref in crit.derived_from:
            expect(
                study.id, ref, (Requirement, Need, DesignRule), "requirement, need or design rule"
            )
    for ref in study.selected:
        if ref not in study.concepts:
            add(
                "TRADE_SELECTION", "error", study.id, f"selected {ref!r} is not one of the concepts"
            )
    if study.decided_by is not None:
        expect(study.id, study.decided_by, (Principal,), "principal")
    for concept, reasons in study.rejected.items():
        if concept not in study.concepts:
            add(
                "TRADE_SELECTION",
                "error",
                study.id,
                f"rejected {concept!r} is not one of the concepts",
            )
        if concept in study.selected:
            add("TRADE_SELECTION", "error", study.id, f"{concept!r} is both selected and rejected")
        unknown = [r for r in reasons if r not in criteria]
        for r in unknown:
            add("TRADE_CRITERION", "error", study.id, f"rejection of {concept} cites unknown {r!r}")
        cited = [criteria[r] for r in reasons if r in criteria]
        if cited and all(c.basis == "reasoned" for c in cited):
            add(
                "TRADE_JUDGEMENT",
                "warning",
                study.id,
                f"rejection of {concept} rests on judgement only",
            )
        for crit in cited:
            _check_rejection_against_scores(study, concept, crit.id, add)
    for concept in study.selected:
        for crit in study.criteria:
            if crit.kind == "gate" and study.scores.get(concept, {}).get(crit.id) is False:
                add(
                    "TRADE_INCONSISTENT",
                    "error",
                    study.id,
                    f"selected {concept} fails gate {crit.id}",
                )


def _check_rejection_against_scores(
    study: TradeStudy, concept: str, crit_id: str, add: _Add
) -> None:
    crit = next(c for c in study.criteria if c.id == crit_id)
    value = study.scores.get(concept, {}).get(crit_id)
    if value is None:
        if crit.basis == "computed":
            add(
                "TRADE_SCORE",
                "warning",
                study.id,
                f"{concept} rejected for computed {crit_id} without a score",
            )
        return
    if crit.kind == "gate":
        if value is not False:
            add(
                "TRADE_INCONSISTENT",
                "error",
                study.id,
                f"{concept} is rejected for gate {crit_id} but passes it",
            )
        return
    peers = [study.scores.get(s, {}).get(crit_id) for s in study.selected]
    numbers = [p for p in peers if isinstance(p, (int, float)) and not isinstance(p, bool)]
    if not numbers or isinstance(value, bool):
        return
    worse = value > max(numbers) if crit.better == "lower" else value < min(numbers)
    if not worse:
        add(
            "TRADE_INCONSISTENT",
            "error",
            study.id,
            f"{concept} is rejected for {crit_id} but is not worse than the selected concepts",
        )


def resolve_value(model: SystemModel, ref: ValueRef) -> Quantity | None:
    """Resolve a ValueRef to a quantity, or None if it does not point at a number."""

    item = model.get(ref.element)
    if item is None:
        return None
    if isinstance(item, Part) and ref.field.startswith("attributes."):
        return item.attributes.get(ref.field.split(".", 1)[1])
    if isinstance(item, Evidence) and ref.field == "value":
        return item.value if isinstance(item.value, Quantity) else None
    value: object = item
    for part in ref.field.split("."):
        if isinstance(value, BaseModel):
            value = getattr(value, part, None)
        elif isinstance(value, dict):
            value = value.get(part)
        else:
            return None
    if isinstance(value, Quantity):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return Quantity(float(value), "")
    return None
