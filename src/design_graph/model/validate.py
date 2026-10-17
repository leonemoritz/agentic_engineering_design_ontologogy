# SPDX-License-Identifier: Apache-2.0
"""Structural and traceability rules for a SystemModel."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel

from design_graph.model.elements import (
    Assumption,
    Evidence,
    Need,
    Part,
    Requirement,
    VerificationCase,
)
from design_graph.model.system import SystemModel, content_hash
from design_graph.model.units import Quantity, same_dimension
from design_graph.model.validate_engineering import check_engineering

Severity = Literal["error", "warning"]
_Add = Callable[[str, Severity, str, str], None]

_ENDPOINTS: dict[str, tuple[tuple[type, ...], tuple[type, ...]]] = {
    "derive": ((Requirement,), (Requirement,)),
    "refine": ((Requirement,), (Need, Requirement)),
    "satisfy": ((Part,), (Requirement,)),
    "verify": ((VerificationCase,), (Requirement,)),
    "allocate": ((Requirement,), (Part,)),
}
_VAGUE = re.compile(
    r"\b(as appropriate|if possible|etc\.?|and/or|adequate|sufficient|user[- ]friendly|"
    r"minimi[sz]e|maximi[sz]e|robust|fast|easy|tbd|tbc)\b",
    re.IGNORECASE,
)


class Finding(BaseModel):
    code: str
    severity: Severity
    element: str
    message: str


def validate_model(model: SystemModel) -> list[Finding]:
    """Return findings; any `error` means the model cannot support an assessment."""

    findings: list[Finding] = []

    def add(code: str, severity: Severity, element: str, message: str) -> None:
        findings.append(Finding(code=code, severity=severity, element=element, message=message))

    counts = Counter(
        item.id
        for name in vars(model)
        if isinstance(getattr(model, name), list)
        for item in getattr(model, name)
    )
    for element_id, count in sorted(counts.items()):
        if count > 1:
            add("DUPLICATE_ID", "error", element_id, f"id used by {count} elements")
    _check_relationships(model, add)
    _check_parts(model, add)
    for requirement in model.requirements:
        _check_requirement(model, requirement, add)
    _check_cases_and_evidence(model, add)
    _check_people(model, add)
    check_engineering(model, add)
    return sorted(findings, key=lambda f: (f.severity != "error", f.element, f.code))


def _check_relationships(model: SystemModel, add: _Add) -> None:
    elements = model.elements()
    for rel in model.relationships:
        source, target = elements.get(rel.source), elements.get(rel.target)
        if source is None or target is None:
            missing = rel.source if source is None else rel.target
            add(
                "REL_DANGLING",
                "error",
                rel.id,
                f"{rel.kind} references unknown element {missing!r}",
            )
            continue
        allowed = _ENDPOINTS.get(rel.kind)
        if allowed and not (isinstance(source, allowed[0]) and isinstance(target, allowed[1])):
            add(
                "REL_TYPE",
                "error",
                rel.id,
                f"{rel.kind} cannot link {type(source).__name__} to {type(target).__name__}",
            )
        if (
            not rel.withdrawn
            and (getattr(source, "withdrawn", False) or getattr(target, "withdrawn", False))
            and rel.kind in {"satisfy", "verify", "allocate"}
        ):
            add("REL_WITHDRAWN", "error", rel.id, f"{rel.kind} touches a withdrawn element")
    for kind in ("derive", "refine"):
        graph: dict[str, list[str]] = {}
        for rel in model.relationships:
            if rel.kind == kind and not rel.withdrawn:
                graph.setdefault(rel.source, []).append(rel.target)
        for start in graph:
            if _reaches(graph, start, start):
                add("REL_CYCLE", "error", start, f"{kind} relationships form a cycle")


def _reaches(graph: dict[str, list[str]], start: str, goal: str) -> bool:
    seen: set[str] = set()
    stack = list(graph.get(start, []))
    while stack:
        node = stack.pop()
        if node == goal:
            return True
        if node not in seen:
            seen.add(node)
            stack.extend(graph.get(node, []))
    return False


def _check_parts(model: SystemModel, add: _Add) -> None:
    part_ids = {part.id for part in model.parts}
    for part in model.parts:
        if part.parent is not None and part.parent not in part_ids:
            add("PART_PARENT", "error", part.id, f"unknown parent {part.parent!r}")
    parents = {part.id: part.parent for part in model.parts}
    for part_id in parents:
        seen = {part_id}
        current = parents.get(part_id)
        while current is not None:
            if current in seen:
                add("PART_CYCLE", "error", part_id, "part hierarchy has a cycle")
                break
            seen.add(current)
            current = parents.get(current)


def _check_requirement(model: SystemModel, req: Requirement, add: _Add) -> None:
    if req.withdrawn:
        return
    subject = next((part for part in model.parts if part.id == req.subject), None)
    if subject is None:
        add("REQ_SUBJECT", "error", req.id, f"unknown subject part {req.subject!r}")
    if "shall" not in req.text.casefold():
        add("REQ_SHALL", "warning", req.id, "requirement text has no 'shall'")
    for hit in sorted({match.group(0).casefold() for match in _VAGUE.finditer(req.text)}):
        add("REQ_VAGUE", "warning", req.id, f"vague or non-verifiable wording: {hit!r}")
    if req.relation is None:
        if req.target or req.upper or req.tolerance or req.attribute:
            add(
                "REQ_QUALITATIVE", "error", req.id, "qualitative requirement carries numeric fields"
            )
    elif req.relation == "bool":
        if req.attribute is None:
            add("REQ_ATTRIBUTE", "error", req.id, "bool requirement needs an attribute name")
    else:
        _check_quantitative(req, subject, add)
    traces_up = _traces_to_need(model, req.id)
    if not traces_up:
        add("REQ_NO_NEED", "error", req.id, "no refine/derive path to a Need")
    has_case = bool(model.related("verify", target=req.id))
    has_children = bool(model.related("derive", target=req.id))
    if not has_case and not has_children:
        add("REQ_NO_VERIFY", "error", req.id, "no verify case and no derived requirements")
    if not model.related("satisfy", target=req.id):
        add("REQ_NO_CLAIM", "warning", req.id, "no part claims to satisfy this requirement")


def _check_quantitative(req: Requirement, subject: Part | None, add: _Add) -> None:
    relation = req.relation
    if req.target is None:
        if not req.tbc:
            add("REQ_TARGET", "error", req.id, f"{relation} requirement needs a target")
        return
    if relation == "range":
        if req.upper is None:
            add("REQ_RANGE", "error", req.id, "range needs target (lower) and upper")
        elif not same_dimension(req.target, req.upper):
            add("REQ_DIMENSION", "error", req.id, "target and upper differ in dimension")
        elif req.upper.to_base() < req.target.to_base():
            add("REQ_RANGE", "error", req.id, "upper is below lower")
    elif req.upper is not None:
        add("REQ_RANGE", "error", req.id, "upper is only valid for range")
    if relation == "eq" and req.tolerance is None:
        add("REQ_TOLERANCE", "error", req.id, "eq requirement needs a tolerance")
    if req.tolerance is not None:
        if not same_dimension(req.target, req.tolerance):
            add("REQ_DIMENSION", "error", req.id, "tolerance differs in dimension from target")
        if req.tolerance.is_offset:
            add("REQ_OFFSET", "error", req.id, "tolerance must use a delta unit, not an offset")
    if subject is not None and req.attribute is not None:
        attribute = subject.attributes.get(req.attribute)
        if attribute is None:
            add(
                "REQ_ATTRIBUTE",
                "warning",
                req.id,
                f"{subject.id} records no design value for {req.attribute!r}",
            )
        elif not same_dimension(attribute, req.target):
            add("REQ_DIMENSION", "error", req.id, "attribute and target differ in dimension")


def _traces_to_need(model: SystemModel, requirement_id: str) -> bool:
    need_ids = {need.id for need in model.needs if not need.withdrawn}
    graph: dict[str, list[str]] = {}
    for rel in model.relationships:
        if rel.kind in {"derive", "refine"} and not rel.withdrawn:
            graph.setdefault(rel.source, []).append(rel.target)
    seen: set[str] = set()
    stack = [requirement_id]
    while stack:
        node = stack.pop()
        if node in need_ids:
            return True
        if node not in seen:
            seen.add(node)
            stack.extend(graph.get(node, []))
    return False


def _check_cases_and_evidence(model: SystemModel, add: _Add) -> None:
    tool_ids = {tool.id for tool in model.tools}
    for case in model.cases:
        if case.withdrawn:
            continue
        if case.tool is not None and case.tool not in tool_ids:
            add("CASE_TOOL", "error", case.id, f"unknown tool declaration {case.tool!r}")
        if case.method in {"A", "T"} and case.tool is None:
            add("CASE_NO_TOOL", "warning", case.id, f"method {case.method} needs a declared tool")
        if not model.related("verify", source=case.id):
            add("CASE_ORPHAN", "warning", case.id, "case verifies no requirement")
    for ev in model.evidence:
        _check_evidence(model, ev, add)
    for assumption in model.assumptions:
        _check_assumption(model, assumption, add)


def _check_evidence(model: SystemModel, ev: Evidence, add: _Add) -> None:
    if ev.requirement not in model.related("verify", source=ev.case):
        add("EVID_LINK", "error", ev.id, "case does not verify this requirement")
    if model.principal(ev.created_by) is None:
        add("EVID_PRINCIPAL", "error", ev.id, f"unknown principal {ev.created_by!r}")
    for attestation in ev.attestations:
        who = model.principal(attestation.principal)
        if who is None:
            add("ATTEST_PRINCIPAL", "error", ev.id, f"unknown principal {attestation.principal!r}")
        elif who.kind != "human" or attestation.role not in who.roles:
            add(
                "ATTEST_UNAUTHORIZED",
                "error",
                ev.id,
                f"{who.id} may not attest as {attestation.role}",
            )
    if isinstance(ev.value, Quantity):
        requirement = next((r for r in model.requirements if r.id == ev.requirement), None)
        if requirement and requirement.target and not same_dimension(ev.value, requirement.target):
            add("EVID_DIMENSION", "error", ev.id, "evidence value differs in dimension from target")


def _check_assumption(model: SystemModel, assumption: Assumption, add: _Add) -> None:
    requirement_ids = {req.id for req in model.requirements}
    for target in assumption.impacts:
        if target not in requirement_ids:
            add("ASSUME_IMPACT", "error", assumption.id, f"unknown requirement {target!r}")
    if assumption.status == "closed" and not assumption.closure.strip():
        add("ASSUME_CLOSURE", "error", assumption.id, "closed assumption needs a closure note")


def _check_people(model: SystemModel, add: _Add) -> None:
    for tool in model.tools:
        who = model.principal(tool.declared_by)
        if who is None or who.kind != "human":
            add("TOOL_DECLARER", "error", tool.id, "tool must be declared by a human principal")
    for approval in model.approvals:
        who = model.principal(approval.principal)
        if who is None or who.kind != "human" or "approver" not in who.roles:
            add("APPROVAL_UNAUTHORIZED", "error", approval.id, "approver must be a human approver")
        if model.get(approval.element) is None:
            add("APPROVAL_DANGLING", "error", approval.id, f"unknown element {approval.element!r}")
        elif approval.content_hash != content_hash(model, approval.element):
            add("APPROVAL_STALE", "warning", approval.id, "element changed since approval")
