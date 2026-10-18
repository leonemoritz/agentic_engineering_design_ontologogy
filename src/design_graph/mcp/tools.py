# SPDX-License-Identifier: Apache-2.0
"""The MCP tools as plain functions: read the model, plan the work, and try proposals.

Every call loads the model file again, so the file stays the only state. Nothing here writes to
disk. `propose_change` runs a proposal through the orchestrator's permission and validation gate
and reports what it would change; the change still has to go through review and a person. Files
are only read below the workspace root. Approvals, attestations, baselines, principals and tool
declarations cannot be written through any tool.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from design_graph.agents import DEFAULT_ROLES, FORBIDDEN_FOR_AGENTS, Orchestrator, Proposal, Task
from design_graph.agents.roles import RoleSpec
from design_graph.model import (
    ModelError,
    Relationship,
    RequirementAssessment,
    SystemModel,
    approval_state,
    assess_model,
    content_hash,
    load_model,
    validate_model,
)
from design_graph.model.matrix import MatrixRow, verification_matrix
from design_graph.model.validate import Finding
from design_graph.process import (
    DEFAULT_TEMPLATE,
    OpenItem,
    ProcessTemplate,
    StageStatus,
    evaluate_process,
    load_template,
)

ErrorCode = Literal[
    "PATH_OUTSIDE_ROOT",
    "FILE_NOT_FOUND",
    "MODEL_UNREADABLE",
    "MODEL_INVALID",
    "TEMPLATE_INVALID",
    "ELEMENT_NOT_FOUND",
    "UNKNOWN_ROLE",
]


class ToolFailure(Exception):
    """An anticipated failure with a machine-readable code; the agent sees a tool error."""

    def __init__(self, code: ErrorCode, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def to_json(self) -> str:
        return json.dumps({"code": self.code, "message": self.message, **self.details})


class Workspace:
    """Resolves agent-supplied paths below one root directory and loads models and templates."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def path(self, value: str) -> Path:
        path = (self.root / value).resolve()
        if not path.is_relative_to(self.root):
            raise ToolFailure("PATH_OUTSIDE_ROOT", "path is outside the workspace root", path=value)
        if not path.is_file():
            raise ToolFailure("FILE_NOT_FOUND", "no such file", path=value)
        return path

    def model(self, model_path: str) -> SystemModel:
        try:
            return load_model(self.path(model_path))
        except ModelError as error:
            raise ToolFailure("MODEL_UNREADABLE", str(error), path=model_path) from error

    def template(self, template_path: str | None) -> ProcessTemplate:
        if template_path is None:
            return DEFAULT_TEMPLATE
        try:
            template = load_template(self.path(template_path))
        except (OSError, ValueError) as error:
            raise ToolFailure("TEMPLATE_INVALID", str(error), path=template_path) from error
        problems = template.check()
        if problems:
            raise ToolFailure(
                "TEMPLATE_INVALID",
                "invalid process template",
                path=template_path,
                problems=problems,
            )
        return template


# ---------------------------------------------------------------------- results


class Item(BaseModel):
    """An open item with its stable id (`rule:element`)."""

    id: str
    rule: str
    stage: str
    severity: str
    element: str
    message: str
    action: str
    owner: str

    @classmethod
    def of(cls, item: OpenItem) -> Item:
        return cls(id=item.id, **item.model_dump())


class ValidationReport(BaseModel):
    valid: bool
    errors: int
    warnings: int
    findings: list[Finding]


class VerificationReport(BaseModel):
    all_satisfied: bool
    summary: dict[str, int]
    requirements: list[RequirementAssessment]


class MatrixReport(BaseModel):
    rows: list[MatrixRow]


class ProcessReport(BaseModel):
    template: str
    complete: bool
    stages: list[StageStatus]
    next_actions: list[Item]
    human_actions: list[Item]


class OpenItemsReport(BaseModel):
    count: int
    items: list[Item]


class TaskView(BaseModel):
    id: str
    role: str
    author: str
    stage: str
    items: list[str]
    writes: list[str]
    brief: str


class PlanReport(BaseModel):
    tasks: list[TaskView]


class RolesReport(BaseModel):
    roles: list[RoleSpec]
    forbidden_for_agents: list[str]


class ElementReport(BaseModel):
    id: str
    collection: str
    type: str
    content_hash: str
    approval: Literal["approved", "stale", "none"]
    element: dict[str, Any]
    relationships_out: list[Relationship]
    relationships_in: list[Relationship]


class SatisfactionChange(BaseModel):
    requirement: str
    before: str | None
    after: str | None


class ProposalReport(BaseModel):
    outcome: str
    accepted: bool
    detail: str
    author: str
    changed: list[str]
    resolved_items: list[str] = Field(default_factory=list)
    new_items: list[Item] = Field(default_factory=list)
    satisfaction_changes: list[SatisfactionChange] = Field(default_factory=list)
    written: Literal[False] = False


# ---------------------------------------------------------------------- tools


def _errors(model: SystemModel) -> list[Finding]:
    return [f for f in validate_model(model) if f.severity == "error"]


def validate(ws: Workspace, model_path: str) -> ValidationReport:
    findings = validate_model(ws.model(model_path))
    errors = sum(1 for f in findings if f.severity == "error")
    return ValidationReport(
        valid=errors == 0, errors=errors, warnings=len(findings) - errors, findings=findings
    )


def _valid_model(ws: Workspace, model_path: str) -> SystemModel:
    model = ws.model(model_path)
    errors = _errors(model)
    if errors:
        raise ToolFailure(
            "MODEL_INVALID",
            f"model has {len(errors)} validation errors; run validate_model first",
            path=model_path,
            findings=[f.model_dump() for f in errors],
        )
    return model


def verification_status(ws: Workspace, model_path: str) -> VerificationReport:
    assessment = assess_model(_valid_model(ws, model_path))
    return VerificationReport(
        all_satisfied=assessment.all_satisfied,
        summary=assessment.summary(),
        requirements=assessment.requirements,
    )


def matrix(ws: Workspace, model_path: str) -> MatrixReport:
    return MatrixReport(rows=verification_matrix(_valid_model(ws, model_path)))


def process_status(
    ws: Workspace, model_path: str, template_path: str | None = None, limit: int = 20
) -> ProcessReport:
    status = evaluate_process(ws.model(model_path), ws.template(template_path))
    return ProcessReport(
        template=status.template,
        complete=status.complete,
        stages=status.stages,
        next_actions=[Item.of(i) for i in status.next_actions()[: max(limit, 0)]],
        human_actions=[Item.of(i) for i in status.human_actions],
    )


def open_items(
    ws: Workspace,
    model_path: str,
    template_path: str | None = None,
    owner: str | None = None,
    include_warnings: bool = False,
) -> OpenItemsReport:
    template = ws.template(template_path)
    status = evaluate_process(ws.model(model_path), template)
    items = sorted(
        (
            i
            for i in status.items
            if (include_warnings or i.severity == "blocking")
            and (owner is None or i.owner == owner)
        ),
        key=lambda i: (template.order(i.stage), i.severity, i.rule),
    )
    return OpenItemsReport(count=len(items), items=[Item.of(i) for i in items])


class _PlanOnly:
    """Planning and dry-run proposals need no backend; executing with this one fails loudly."""

    def execute(self, task: Any, model: Any) -> Any:
        raise RuntimeError("planning only")

    def review(self, task: Any, model: Any) -> Any:
        raise RuntimeError("planning only")


def plan(ws: Workspace, model_path: str, template_path: str | None = None) -> PlanReport:
    orchestrator = Orchestrator(
        ws.model(model_path), _PlanOnly(), template=ws.template(template_path)
    )
    return PlanReport(
        tasks=[
            TaskView(
                id=t.id,
                role=t.role,
                author=t.author,
                stage=t.stage,
                items=[i.id for i in t.items],
                writes=t.writes,
                brief=t.brief,
            )
            for t in orchestrator.plan()
        ]
    )


def roles() -> RolesReport:
    return RolesReport(
        roles=list(DEFAULT_ROLES.values()), forbidden_for_agents=sorted(FORBIDDEN_FOR_AGENTS)
    )


def element(ws: Workspace, model_path: str, element_id: str) -> ElementReport:
    model = ws.model(model_path)
    for collection, value in model:
        if not isinstance(value, list):
            continue
        found = next((e for e in value if e.id == element_id), None)
        if found is None:
            continue
        rels = [r for r in model.relationships if not r.withdrawn]
        return ElementReport(
            id=element_id,
            collection=collection,
            type=type(found).__name__,
            content_hash=content_hash(model, element_id),
            approval=approval_state(model, element_id),
            element=found.model_dump(mode="json"),
            relationships_out=[r for r in rels if r.source == element_id],
            relationships_in=[r for r in rels if r.target == element_id],
        )
    raise ToolFailure("ELEMENT_NOT_FOUND", "no element with this id", element=element_id)


def propose_change(
    ws: Workspace,
    model_path: str,
    role: str,
    upserts: dict[str, list[dict[str, Any]]],
    rationale: str = "",
    template_path: str | None = None,
) -> ProposalReport:
    template = ws.template(template_path)
    orchestrator = Orchestrator(ws.model(model_path), _PlanOnly(), template=template)
    author = orchestrator.principals.get(role)
    if author is None:
        raise ToolFailure(
            "UNKNOWN_ROLE",
            "not an agent role; see list_roles",
            role=role,
            roles=sorted(orchestrator.principals),
        )
    spec = orchestrator.roles[role]
    task = Task(
        id="proposal",
        round=0,
        role=role,
        author=author,
        stage=spec.stages[0] if spec.stages else "",
        items=[],
        brief="",
        writes=spec.writes,
    )
    proposal = Proposal(author=author, upserts=upserts, rationale=rationale)
    candidate, outcome, detail, changed = orchestrator.apply(task, proposal)
    report = ProposalReport(
        outcome=outcome,
        accepted=candidate is not None,
        detail=detail,
        author=author,
        changed=changed,
    )
    if candidate is None:
        return report
    before = orchestrator.model
    old = {i.id: i for i in evaluate_process(before, template).items}
    new = {i.id: i for i in evaluate_process(candidate, template).items}
    report.resolved_items = sorted(set(old) - set(new))
    report.new_items = [Item.of(new[k]) for k in sorted(set(new) - set(old))]
    if not _errors(before) and not _errors(candidate):
        was = {r.requirement: r.satisfaction.value for r in assess_model(before).requirements}
        now = {r.requirement: r.satisfaction.value for r in assess_model(candidate).requirements}
        report.satisfaction_changes = [
            SatisfactionChange(requirement=r, before=was.get(r), after=now.get(r))
            for r in sorted(set(was) | set(now))
            if was.get(r) != now.get(r)
        ]
    return report
