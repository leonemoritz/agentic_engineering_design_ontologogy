# SPDX-License-Identifier: Apache-2.0
"""Completeness and consistency rules.

Each failed rule is an open item: what is missing, where in the process it belongs, what to do, and
who should do it.

These rules turn the model's end-state checks into guidance. Validation says a model is malformed;
these rules say what an engineer would ask next ("which failure modes cover this load case?", "what
was this FEM compared with?", "who reviewed this trade study?").
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from design_graph.model.assess import Assessment, VerificationStatus, approval_state, assess_model
from design_graph.model.elements import (
    Approval,
    Assumption,
    Evidence,
    Need,
    Part,
    Requirement,
    ToolDeclaration,
    VerificationCase,
)
from design_graph.model.engineering import (
    Allowable,
    Concept,
    ConsistencyCheck,
    Credibility,
    DesignRule,
    FailureMode,
    LoadCase,
    OptimizationSpec,
    Review,
    Statement,
    TradeStudy,
)
from design_graph.model.system import SystemModel, content_hash
from design_graph.model.units import same_dimension
from design_graph.model.validate import Finding, validate_model
from design_graph.model.validate_engineering import resolve_value

ItemSeverity = Literal["blocking", "warning"]
HUMAN = "human"

STAGE_OWNER: dict[str, str] = {
    "requirements": "requirements_engineer",
    "concepts": "concept_engineer",
    "allowables": "materials_engineer",
    "sizing": "structures_analyst",
    "analysis": "simulation_credibility",
    "verification": "structures_analyst",
    "test": "test_engineer",
}

_STAGE_OF_TYPE: dict[type, str] = {
    Statement: "requirements",
    Need: "requirements",
    Requirement: "requirements",
    Assumption: "requirements",
    DesignRule: "requirements",
    Part: "sizing",
    Concept: "concepts",
    TradeStudy: "concepts",
    Allowable: "allowables",
    LoadCase: "sizing",
    FailureMode: "sizing",
    OptimizationSpec: "sizing",
    VerificationCase: "analysis",
    ToolDeclaration: "analysis",
    Evidence: "analysis",
    Credibility: "analysis",
    ConsistencyCheck: "analysis",
    Review: "verification",
    Approval: "verification",
}

# Element types that need an independent, accepted review before their stage can close.
REVIEW_GATED: dict[type, ItemSeverity] = {
    TradeStudy: "blocking",
    Credibility: "blocking",
    OptimizationSpec: "warning",
    FailureMode: "warning",
    LoadCase: "warning",
    Allowable: "warning",
}

_HUMAN_CODES = {"TOOL_DECLARER", "APPROVAL_UNAUTHORIZED", "APPROVAL_STALE", "ATTEST_UNAUTHORIZED"}


class OpenItem(BaseModel):
    rule: str
    stage: str
    severity: ItemSeverity
    element: str
    message: str
    action: str
    owner: str

    @property
    def id(self) -> str:
        return f"{self.rule}:{self.element}"


@dataclass
class Context:
    model: SystemModel
    findings: list[Finding]
    assessment: Assessment | None

    def stage_of(self, element_id: str) -> str:
        item = self.model.get(element_id)
        return (
            _STAGE_OF_TYPE.get(type(item), "requirements") if item is not None else "requirements"
        )


RuleFn = Callable[[Context], list[OpenItem]]


@dataclass(frozen=True)
class RuleSpec:
    id: str
    stage: str
    description: str
    fn: RuleFn


RULES: list[RuleSpec] = []


def process_rule(rule_id: str, stage: str, description: str) -> Callable[[RuleFn], RuleFn]:
    def register(fn: RuleFn) -> RuleFn:
        RULES.append(RuleSpec(rule_id, stage, description, fn))
        return fn

    return register


def _item(
    rule: str,
    stage: str,
    severity: ItemSeverity,
    element: str,
    message: str,
    action: str,
    owner: str | None = None,
) -> OpenItem:
    return OpenItem(
        rule=rule,
        stage=stage,
        severity=severity,
        element=element,
        message=message,
        action=action,
        owner=owner or STAGE_OWNER.get(stage, "manager"),
    )


def _touches(model: SystemModel, element_id: str, kind: str = "trace") -> bool:
    return any(
        not r.withdrawn and r.kind == kind and element_id in (r.source, r.target)
        for r in model.relationships
    )


# ------------------------------------------------------------------ requirements


@process_rule("PR-VALIDATION", "requirements", "Model validation findings")
def _validation(ctx: Context) -> list[OpenItem]:
    out = []
    for f in ctx.findings:
        stage = ctx.stage_of(f.element)
        owner = HUMAN if f.code in _HUMAN_CODES else STAGE_OWNER.get(stage, "quality_reviewer")
        severity: ItemSeverity = "blocking" if f.severity == "error" else "warning"
        out.append(
            _item(
                "PR-VALIDATION",
                stage,
                severity,
                f.element,
                f"{f.code}: {f.message}",
                "Correct the element so validation passes.",
                owner,
            )
        )
    return out


@process_rule("PR-STATEMENT-TRACE", "requirements", "Every stakeholder statement is traced")
def _statement_trace(ctx: Context) -> list[OpenItem]:
    return [
        _item(
            "PR-STATEMENT-TRACE",
            "requirements",
            "blocking",
            s.id,
            f"Statement not traced to any need or requirement: {s.text[:80]!r}",
            "Add a trace relationship from the need or requirement that interprets it, or "
            "record why it needs none.",
        )
        for s in ctx.model.statements
        if not s.withdrawn and not _touches(ctx.model, s.id)
    ]


@process_rule("PR-NEED-SOURCE", "requirements", "Every need traces to a statement")
def _need_source(ctx: Context) -> list[OpenItem]:
    if not ctx.model.statements:
        return []
    return [
        _item(
            "PR-NEED-SOURCE",
            "requirements",
            "warning",
            n.id,
            "Need has no trace to a stakeholder statement.",
            "Trace it to the statement it comes from, or mark it as derived by the engineer.",
        )
        for n in ctx.model.needs
        if not n.withdrawn and not _touches(ctx.model, n.id)
    ]


@process_rule("PR-ASSUMPTION-PLAN", "requirements", "Open assumptions have a closure plan")
def _assumption_plan(ctx: Context) -> list[OpenItem]:
    return [
        _item(
            "PR-ASSUMPTION-PLAN",
            "requirements",
            "warning",
            a.id,
            "Open assumption has no closure plan.",
            "State how and when it will be confirmed (closure field), and who owns it.",
        )
        for a in ctx.model.assumptions
        if a.status == "open" and not a.withdrawn and not a.closure.strip()
    ]


# ------------------------------------------------------------------ concepts


@process_rule("PR-CONCEPT", "concepts", "A concept is selected by a trade study")
def _concept(ctx: Context) -> list[OpenItem]:
    if not ctx.model.requirements:
        return []
    if any(t.selected and not t.withdrawn for t in ctx.model.trade_studies):
        return []
    return [
        _item(
            "PR-CONCEPT",
            "concepts",
            "blocking",
            "-",
            "No trade study selects a concept; the design rests on an unscreened choice.",
            "List alternative concepts, derive gates and criteria from the requirements, "
            "compute the criteria and record the decision.",
        )
    ]


# ------------------------------------------------------------------ allowables


@process_rule("PR-ALLOWABLES", "allowables", "Modelled failure modes have allowables")
def _allowables(ctx: Context) -> list[OpenItem]:
    modelled = [m for m in ctx.model.failure_modes if m.status == "modelled" and not m.withdrawn]
    if not modelled or ctx.model.allowables:
        return []
    return [
        _item(
            "PR-ALLOWABLES",
            "allowables",
            "blocking",
            "-",
            "Failure modes are modelled but no material allowables are recorded.",
            "Record the strength and stiffness values used, with source, basis and status.",
        )
    ]


@process_rule("PR-ALLOWABLE-TEST", "test", "Allowables are confirmed by coupon tests")
def _allowable_test(ctx: Context) -> list[OpenItem]:
    return [
        _item(
            "PR-ALLOWABLE-TEST",
            "test",
            "warning",
            a.id,
            f"{a.material} {a.property} is {a.status}, not coupon tested.",
            "Plan coupon tests and replace the value with the measured characteristic value.",
        )
        for a in ctx.model.allowables
        if a.status != "coupon_tested" and not a.withdrawn
    ]


# ------------------------------------------------------------------ sizing


@process_rule(
    "PR-LOADCASE-COVERAGE", "sizing", "Load-case requirements are covered by failure modes"
)
def _loadcase_coverage(ctx: Context) -> list[OpenItem]:
    covered = {r for m in ctx.model.failure_modes if not m.withdrawn for r in m.covers}
    out = []
    for lc in ctx.model.load_cases:
        for req in lc.requirements:
            if req not in covered:
                out.append(
                    _item(
                        "PR-LOADCASE-COVERAGE",
                        "sizing",
                        "blocking",
                        req,
                        f"{req} is loaded in {lc.id} but no failure mode covers it.",
                        "List every way the design can fail under this load case "
                        "(strength, stability, joints, supports, interfaces).",
                    )
                )
    return out


@process_rule("PR-FM-LOADCASE", "sizing", "Failure modes refer to a defined load case")
def _fm_loadcase(ctx: Context) -> list[OpenItem]:
    if not ctx.model.failure_modes:
        return []
    loaded = {r for lc in ctx.model.load_cases for r in lc.requirements}
    return [
        _item(
            "PR-FM-LOADCASE",
            "sizing",
            "warning",
            m.id,
            "Failure mode covers requirements that no load case defines.",
            "Define the load case (magnitude, location, supports) the mode is assessed for.",
        )
        for m in ctx.model.failure_modes
        if not m.withdrawn and not set(m.covers) & loaded
    ]


@process_rule("PR-FM-ANALYSIS", "sizing", "Modelled failure modes have an analysis case")
def _fm_analysis(ctx: Context) -> list[OpenItem]:
    return [
        _item(
            "PR-FM-ANALYSIS",
            "sizing",
            "blocking",
            m.id,
            "Modelled failure mode has no analysis case.",
            "Link the verification case (method A) whose analysis evaluates this mode.",
        )
        for m in ctx.model.failure_modes
        if m.status == "modelled" and not m.analysis and not m.withdrawn
    ]


@process_rule("PR-OPTIMIZATION", "sizing", "Sizing is declared as an optimisation spec")
def _optimization(ctx: Context) -> list[OpenItem]:
    if not ctx.model.failure_modes or ctx.model.optimizations:
        return []
    return [
        _item(
            "PR-OPTIMIZATION",
            "sizing",
            "warning",
            "-",
            "Failure modes exist but the sizing problem is not declared.",
            "Record the design variables with bounds, the objectives and the constraints.",
        )
    ]


# ------------------------------------------------------------------ analysis


@process_rule(
    "PR-CREDIBILITY", "analysis", "Analyses have a credibility record and a higher tier check"
)
def _credibility(ctx: Context) -> list[OpenItem]:
    records = {c.case: c for c in ctx.model.credibility if not c.withdrawn}
    used = {case for m in ctx.model.failure_modes if not m.withdrawn for case in m.analysis}
    out = []
    for case_id in sorted(used):
        rec = records.get(case_id)
        if rec is None:
            out.append(
                _item(
                    "PR-CREDIBILITY",
                    "analysis",
                    "blocking",
                    case_id,
                    "Analysis used for failure modes has no credibility record.",
                    "Record its tier, convergence, what it was compared with, and limits.",
                )
            )
            continue
        if rec.tier == 1 and rec.compared_with is None and not rec.waiver.strip():
            out.append(
                _item(
                    "PR-CREDIBILITY",
                    "analysis",
                    "blocking",
                    case_id,
                    "Tier-1 (analytical) result has no higher-tier comparison.",
                    "Compare it with a finer model (FEM) or a test, or record a waiver.",
                )
            )
        if rec.tier >= 2 and (
            rec.convergence_change_pct is None or abs(rec.convergence_change_pct) > 10
        ):
            out.append(
                _item(
                    "PR-CREDIBILITY",
                    "analysis",
                    "warning",
                    case_id,
                    "Numerical result without a converged mesh study (change > 10 % or none).",
                    "Run at least two mesh sizes and record the change.",
                )
            )
    return out


@process_rule("PR-CONSISTENCY", "analysis", "Independent models agree within tolerance")
def _consistency(ctx: Context) -> list[OpenItem]:
    out = []
    for check in ctx.model.consistency_checks:
        if check.withdrawn:
            continue
        a, b = resolve_value(ctx.model, check.a), resolve_value(ctx.model, check.b)
        if a is None or b is None:
            continue
        if not same_dimension(a, b):
            out.append(
                _item(
                    "PR-CONSISTENCY",
                    check.feedback_stage,
                    "blocking",
                    check.id,
                    "Compared values differ in dimension.",
                    "Fix the consistency check.",
                )
            )
            continue
        ref = b.to_base()
        deviation = abs(a.to_base() / ref - 1) if ref else float("inf")
        if deviation > check.tolerance:
            out.append(
                _item(
                    "PR-CONSISTENCY",
                    check.feedback_stage,
                    "blocking",
                    check.id,
                    f"{check.a.element}.{check.a.field} and {check.b.element}.{check.b.field} "
                    f"differ by {100 * deviation:.1f} % (tolerance {100 * check.tolerance:.0f} %).",
                    check.action
                    or f"Rework at stage {check.feedback_stage}: find which model "
                    "is wrong, correct it and re-run everything downstream.",
                )
            )
    return out


# ------------------------------------------------------------------ verification


@process_rule(
    "PR-FM-VALIDATION", "verification", "Failure modes are validated by test or calibration"
)
def _fm_validation(ctx: Context) -> list[OpenItem]:
    validated_cases = {c.case for c in ctx.model.credibility if c.validated_by}
    out = []
    for m in ctx.model.failure_modes:
        if m.withdrawn:
            continue
        tested = any(
            isinstance(case := ctx.model.get(c), VerificationCase) and case.method == "T"
            for r in m.covers
            for c in ctx.model.related("verify", target=r)
        )
        if not tested and not set(m.analysis) & validated_cases:
            out.append(
                _item(
                    "PR-FM-VALIDATION",
                    "verification",
                    "warning",
                    m.id,
                    "No test case and no validated analysis for this failure mode.",
                    "Add a test case for a covered requirement, or validate the analysis.",
                    "test_engineer",
                )
            )
    return out


@process_rule("PR-REVIEW", "verification", "Gated elements have an accepted independent review")
def _review(ctx: Context) -> list[OpenItem]:
    out = []
    for name in (
        "trade_studies",
        "credibility",
        "optimizations",
        "failure_modes",
        "load_cases",
        "allowables",
    ):
        for element in getattr(ctx.model, name):
            if element.withdrawn:
                continue
            severity = REVIEW_GATED[type(element)]
            current = content_hash(ctx.model, element.id)
            reviews = [
                r
                for r in ctx.model.reviews
                if element.id in r.elements
                and not r.withdrawn
                and r.reviewer != r.author
                and r.content_hashes.get(element.id, current) == current
            ]
            if any(r.accepted for r in reviews):
                continue
            rejected = [r for r in reviews if not r.accepted]
            stage = ctx.stage_of(element.id)
            if rejected:
                findings = "; ".join(
                    f.text
                    for r in rejected
                    for f in r.findings
                    if f.severity in ("blocking", "major") and not f.resolved
                )
                out.append(
                    _item(
                        "PR-REVIEW",
                        stage,
                        "blocking",
                        element.id,
                        f"Review found open issues: {findings}",
                        "Resolve the findings and request a new review.",
                    )
                )
            else:
                out.append(
                    _item(
                        "PR-REVIEW",
                        stage,
                        severity,
                        element.id,
                        "Not reviewed by an independent reviewer (or changed since).",
                        "Request an independent review of the element.",
                        "quality_reviewer",
                    )
                )
    return out


@process_rule(
    "PR-EVIDENCE", "verification", "Every verification case has current, passing evidence"
)
def _evidence(ctx: Context) -> list[OpenItem]:
    if ctx.assessment is None:
        return []
    cases = {c.id: c for c in ctx.model.cases}
    out = []
    for req in ctx.assessment.requirements:
        for case in req.cases:
            if case.status == VerificationStatus.VERIFIED:
                continue
            method = cases[case.case].method if case.case in cases else "A"
            if case.status == VerificationStatus.FAILED:
                out.append(
                    _item(
                        "PR-EVIDENCE",
                        "sizing",
                        "blocking",
                        f"{req.requirement}/{case.case}",
                        "Evidence shows the requirement is not met.",
                        "Rework the design (sizing) and re-verify.",
                        "structures_analyst",
                    )
                )
                continue
            owner = "structures_analyst" if method == "A" else HUMAN
            stage = "test" if method == "T" else "verification"
            out.append(
                _item(
                    "PR-EVIDENCE",
                    stage,
                    "blocking",
                    f"{req.requirement}/{case.case}",
                    f"Case {case.case} ({method}) is {case.status.value.lower()}.",
                    "Produce current evidence from the declared tool."
                    if method == "A"
                    else "A person must perform and attest this inspection, review or test.",
                    owner,
                )
            )
    return out


@process_rule("PR-APPROVAL", "verification", "Requirements and cases are approved by a human")
def _approval(ctx: Context) -> list[OpenItem]:
    out = []
    for element in [*ctx.model.requirements, *ctx.model.cases]:
        if element.withdrawn:
            continue
        state = approval_state(ctx.model, element.id)
        if state != "approved":
            out.append(
                _item(
                    "PR-APPROVAL",
                    "verification",
                    "blocking",
                    element.id,
                    f"Approval is {state}.",
                    "A human approver reviews and approves it.",
                    HUMAN,
                )
            )
    return out


def open_items(model: SystemModel) -> list[OpenItem]:
    """Run every rule. The model is assessed only if it validates without errors."""

    findings = validate_model(model)
    assessment = None
    if not any(f.severity == "error" for f in findings):
        assessment = assess_model(model)
    ctx = Context(model=model, findings=findings, assessment=assessment)
    items: dict[str, OpenItem] = {}
    for spec in RULES:
        for item in spec.fn(ctx):
            items.setdefault(item.id + "@" + item.message, item)
    return list(items.values())
