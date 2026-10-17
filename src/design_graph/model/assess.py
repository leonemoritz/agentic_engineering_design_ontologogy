# SPDX-License-Identifier: Apache-2.0
"""Derived verification and satisfaction status. Nothing here is asserted by the model author."""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from design_graph.model.elements import Evidence, Requirement, VerificationCase
from design_graph.model.system import SystemModel, basis_hash, content_hash
from design_graph.model.units import Quantity, same_dimension


class VerificationStatus(StrEnum):
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    STALE = "STALE"
    INCOMPLETE = "INCOMPLETE"
    UNVERIFIED = "UNVERIFIED"


class SatisfactionStatus(StrEnum):
    SATISFIED = "SATISFIED"
    NOT_SATISFIED = "NOT_SATISFIED"
    OPEN = "OPEN"
    BLOCKED = "BLOCKED"
    WITHDRAWN = "WITHDRAWN"


class Reason(BaseModel):
    code: str
    message: str


class EvidenceAssessment(BaseModel):
    evidence: str
    outcome: Literal["pass", "fail", "incomplete", "stale", "invalid"]
    margin: Quantity | None = None
    reasons: list[Reason] = Field(default_factory=list)


class CaseAssessment(BaseModel):
    case: str
    method: str
    status: VerificationStatus
    deciding_evidence: str | None = None
    evidence: list[EvidenceAssessment] = Field(default_factory=list)
    reasons: list[Reason] = Field(default_factory=list)


class RequirementAssessment(BaseModel):
    requirement: str
    verification: VerificationStatus
    satisfaction: SatisfactionStatus
    margin: Quantity | None = None
    cases: list[CaseAssessment] = Field(default_factory=list)
    derived: list[str] = Field(default_factory=list)
    reasons: list[Reason] = Field(default_factory=list)


class Assessment(BaseModel):
    requirements: list[RequirementAssessment]

    def by_id(self) -> dict[str, RequirementAssessment]:
        return {item.requirement: item for item in self.requirements}

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.requirements:
            counts[item.satisfaction.value] = counts.get(item.satisfaction.value, 0) + 1
        return counts

    @property
    def all_satisfied(self) -> bool:
        active = [r for r in self.requirements if r.satisfaction != SatisfactionStatus.WITHDRAWN]
        return bool(active) and all(r.satisfaction == SatisfactionStatus.SATISFIED for r in active)


def approval_state(model: SystemModel, element_id: str) -> Literal["approved", "stale", "none"]:
    """Approved only by a human approver against the element's current content hash."""

    current = content_hash(model, element_id)
    state: Literal["approved", "stale", "none"] = "none"
    for approval in model.approvals:
        if approval.withdrawn or approval.element != element_id:
            continue
        who = model.principal(approval.principal)
        if who is None or who.kind != "human" or "approver" not in who.roles:
            continue
        if approval.content_hash == current:
            return "approved"
        state = "stale"
    return state


def assess_model(model: SystemModel) -> Assessment:
    """Assess every requirement. Validate the model first; this assumes it is well formed."""

    memo: dict[str, RequirementAssessment] = {}
    visiting: set[str] = set()
    by_id = {req.id: req for req in model.requirements}

    def assess(req: Requirement) -> RequirementAssessment:
        if req.id in memo:
            return memo[req.id]
        if req.id in visiting:
            return RequirementAssessment(
                requirement=req.id,
                verification=VerificationStatus.UNVERIFIED,
                satisfaction=SatisfactionStatus.BLOCKED,
                reasons=[Reason(code="CYCLE", message="derive relationships form a cycle")],
            )
        visiting.add(req.id)
        result = _assess_requirement(model, req, by_id, assess)
        visiting.discard(req.id)
        memo[req.id] = result
        return result

    return Assessment(requirements=[assess(req) for req in model.requirements])


def _assess_requirement(
    model: SystemModel,
    req: Requirement,
    by_id: dict[str, Requirement],
    assess: _Recurse,
) -> RequirementAssessment:
    if req.withdrawn:
        return RequirementAssessment(
            requirement=req.id,
            verification=VerificationStatus.UNVERIFIED,
            satisfaction=SatisfactionStatus.WITHDRAWN,
        )
    reasons: list[Reason] = []
    blockers: list[Reason] = []

    def block(code: str, message: str) -> None:
        blockers.append(Reason(code=code, message=message))

    approval = approval_state(model, req.id)
    if approval != "approved":
        block("REQ_NOT_APPROVED", f"requirement approval is {approval}")
    if req.tbc:
        block("REQ_TBC", "requirement is still TBC")
    for assumption in model.assumptions:
        if (
            assumption.status == "open"
            and not assumption.withdrawn
            and req.id in assumption.impacts
        ):
            block("ASSUMPTION_OPEN", f"open assumption {assumption.id}: {assumption.statement}")
    claims = [
        part_id
        for part_id in model.related("satisfy", target=req.id)
        if any(part.id == part_id and not part.withdrawn for part in model.parts)
    ]
    if not claims:
        block("NO_DESIGN_CLAIM", "no part claims to satisfy this requirement")

    child_ids = [cid for cid in model.related("derive", target=req.id) if cid in by_id]
    children = [assess(by_id[cid]) for cid in child_ids if not by_id[cid].withdrawn]

    case_results = [
        _assess_case(model, req, case)
        for case in model.cases
        if not case.withdrawn and req.id in model.related("verify", source=case.id)
    ]
    verification = _combine([case.status for case in case_results])
    if not case_results:
        if children:
            all_done = all(c.satisfaction == SatisfactionStatus.SATISFIED for c in children)
            verification = (
                VerificationStatus.VERIFIED if all_done else VerificationStatus.INCOMPLETE
            )
            reasons.append(
                Reason(code="BY_DECOMPOSITION", message="verified through derived requirements")
            )
        else:
            reasons.append(Reason(code="NO_VERIFICATION", message="no verification case"))
    for case in case_results:
        reasons.extend(case.reasons)

    margins = [
        e.margin
        for case in case_results
        for e in case.evidence
        if e.margin is not None and e.evidence == case.deciding_evidence
    ]
    margin = min(margins, key=lambda q: q.value) if margins else None

    failed_children = [
        c.requirement for c in children if c.satisfaction == SatisfactionStatus.NOT_SATISFIED
    ]
    open_children = [
        c.requirement for c in children if c.satisfaction != SatisfactionStatus.SATISFIED
    ]
    for child_id in open_children:
        reasons.append(
            Reason(
                code="CHILD_NOT_SATISFIED",
                message=f"derived requirement {child_id} is not satisfied",
            )
        )

    if verification == VerificationStatus.FAILED or failed_children:
        satisfaction = SatisfactionStatus.NOT_SATISFIED
    elif blockers:
        satisfaction = SatisfactionStatus.BLOCKED
    elif verification == VerificationStatus.VERIFIED and not open_children:
        satisfaction = SatisfactionStatus.SATISFIED
    else:
        satisfaction = SatisfactionStatus.OPEN
    return RequirementAssessment(
        requirement=req.id,
        verification=verification,
        satisfaction=satisfaction,
        margin=margin,
        cases=case_results,
        derived=child_ids,
        reasons=blockers + reasons,
    )


def _combine(statuses: list[VerificationStatus]) -> VerificationStatus:
    if not statuses:
        return VerificationStatus.UNVERIFIED
    if VerificationStatus.FAILED in statuses:
        return VerificationStatus.FAILED
    if all(s == VerificationStatus.VERIFIED for s in statuses):
        return VerificationStatus.VERIFIED
    if VerificationStatus.STALE in statuses:
        return VerificationStatus.STALE
    if any(s in {VerificationStatus.VERIFIED, VerificationStatus.INCOMPLETE} for s in statuses):
        return VerificationStatus.INCOMPLETE
    return VerificationStatus.UNVERIFIED


def _assess_case(model: SystemModel, req: Requirement, case: VerificationCase) -> CaseAssessment:
    reasons: list[Reason] = []
    if approval_state(model, case.id) != "approved":
        reasons.append(Reason(code="CASE_NOT_APPROVED", message=f"case {case.id} is not approved"))
        return CaseAssessment(
            case=case.id, method=case.method, status=VerificationStatus.INCOMPLETE, reasons=reasons
        )
    current_basis = basis_hash(model, req.id, case.id)
    evaluated = [
        (ev, _assess_evidence(model, req, case, ev, current_basis))
        for ev in model.evidence
        if not ev.withdrawn and ev.case == case.id and ev.requirement == req.id
    ]
    results = [result for _, result in evaluated]
    live = [(ev, r) for ev, r in evaluated if r.outcome != "stale"]
    if not evaluated:
        reasons.append(Reason(code="NO_EVIDENCE", message=f"no evidence for case {case.id}"))
        return CaseAssessment(
            case=case.id, method=case.method, status=VerificationStatus.UNVERIFIED, reasons=reasons
        )
    if not live:
        reasons.append(
            Reason(code="EVIDENCE_STALE", message=f"all evidence for {case.id} predates a change")
        )
        return CaseAssessment(
            case=case.id,
            method=case.method,
            status=VerificationStatus.STALE,
            evidence=results,
            reasons=reasons,
        )
    ev, decided = max(live, key=lambda pair: (pair[0].date, pair[0].id))
    status = {
        "pass": VerificationStatus.VERIFIED,
        "fail": VerificationStatus.FAILED,
        "incomplete": VerificationStatus.INCOMPLETE,
        "invalid": VerificationStatus.INCOMPLETE,
    }[decided.outcome]
    reasons.extend(decided.reasons)
    if decided.outcome == "fail":
        reasons.append(Reason(code="EVIDENCE_FAILED", message=f"evidence {ev.id} fails {req.id}"))
    return CaseAssessment(
        case=case.id,
        method=case.method,
        status=status,
        deciding_evidence=ev.id,
        evidence=results,
        reasons=reasons,
    )


def _assess_evidence(
    model: SystemModel,
    req: Requirement,
    case: VerificationCase,
    ev: Evidence,
    current_basis: str,
) -> EvidenceAssessment:
    reasons: list[Reason] = []
    if ev.basis_hash != current_basis:
        reasons.append(
            Reason(
                code="EVIDENCE_STALE",
                message=f"{ev.id}: requirement, case, subject or tool changed",
            )
        )
        return EvidenceAssessment(evidence=ev.id, outcome="stale", reasons=reasons)

    margin: Quantity | None = None
    computed: bool | None = None
    if req.relation is not None and ev.value is not None:
        try:
            computed, margin = _compute(req, ev)
        except ValueError as error:
            reasons.append(Reason(code="EVIDENCE_INVALID", message=f"{ev.id}: {error}"))
            return EvidenceAssessment(evidence=ev.id, outcome="invalid", reasons=reasons)

    needs_value = req.relation is not None and case.method in {"A", "T"}
    if needs_value and computed is None:
        reasons.append(
            Reason(
                code="EVIDENCE_NO_VALUE",
                message=f"{ev.id}: method {case.method} needs a measured value",
            )
        )
    if case.method == "A" and req.relation is not None and ev.provenance is None:
        reasons.append(
            Reason(
                code="EVIDENCE_NO_PROVENANCE", message=f"{ev.id}: analysis needs graph provenance"
            )
        )
    if case.method in {"A", "T"} and case.tool is None:
        reasons.append(Reason(code="EVIDENCE_NO_TOOL", message=f"{ev.id}: case declares no tool"))

    required: list[str] = []
    if case.method in {"R", "I"} or (case.method == "A" and req.relation is None):
        required = ["verifier"]
    elif case.method == "T":
        required = ["witness"]
    valid = [a for a in ev.attestations if _may_attest(model, a.principal, a.role)]
    if len(valid) != len(ev.attestations):
        reasons.append(
            Reason(
                code="ATTEST_UNAUTHORIZED",
                message=f"{ev.id}: attestation by an unauthorized principal ignored",
            )
        )
    if any(a.outcome == "fail" for a in valid):
        reasons.append(Reason(code="ATTEST_FAIL", message=f"{ev.id}: attested as failed"))
        return EvidenceAssessment(evidence=ev.id, outcome="fail", margin=margin, reasons=reasons)
    for role in required:
        if not any(a.role == role and a.outcome == "pass" for a in valid):
            reasons.append(
                Reason(code="ATTEST_MISSING", message=f"{ev.id}: needs a {role} attestation")
            )
    if computed is False:
        return EvidenceAssessment(evidence=ev.id, outcome="fail", margin=margin, reasons=reasons)
    if reasons:
        return EvidenceAssessment(
            evidence=ev.id, outcome="incomplete", margin=margin, reasons=reasons
        )
    return EvidenceAssessment(evidence=ev.id, outcome="pass", margin=margin)


def _may_attest(model: SystemModel, principal_id: str, role: str) -> bool:
    who = model.principal(principal_id)
    return who is not None and who.kind == "human" and role in who.roles


def _compute(req: Requirement, ev: Evidence) -> tuple[bool, Quantity | None]:
    """Evaluate a value against the requirement; margin is in coherent base units."""

    value = ev.value
    if req.relation == "bool":
        if not isinstance(value, bool):
            raise ValueError("bool requirement needs a bool value")
        return value == req.expected, None
    if not isinstance(value, Quantity) or req.target is None:
        raise ValueError("numeric requirement needs a quantity value and a target")
    if not same_dimension(value, req.target):
        raise ValueError(
            f"value [{value.dimension}] does not match target [{req.target.dimension}]"
        )
    uncertainty = 0.0
    if ev.uncertainty is not None:
        if not same_dimension(ev.uncertainty, req.target):
            raise ValueError("uncertainty differs in dimension from target")
        if ev.uncertainty.is_offset:
            raise ValueError("uncertainty must use a delta unit, not an offset")
        uncertainty = abs(ev.uncertainty.to_base())
    measured, target = value.to_base(), req.target.to_base()
    if req.relation == "le":
        margin = target - measured - uncertainty
    elif req.relation == "ge":
        margin = measured - target - uncertainty
    elif req.relation == "eq":
        if req.tolerance is None:
            raise ValueError("eq requirement has no tolerance")
        margin = abs(req.tolerance.to_base()) - abs(measured - target) - uncertainty
    elif req.relation == "range":
        if req.upper is None:
            raise ValueError("range requirement has no upper bound")
        margin = min(measured - target, req.upper.to_base() - measured) - uncertainty
    else:
        raise ValueError(f"unsupported relation {req.relation!r}")
    return margin >= 0, Quantity(margin, req.target.base_unit)


_Recurse = Callable[[Requirement], RequirementAssessment]
