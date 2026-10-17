# SPDX-License-Identifier: Apache-2.0
"""Orchestrator: turns open items into role tasks, applies proposals under permission and validation
checks, and sends every accepted change to an independent reviewer before it enters the model.

The model is the only shared state. Authors and reviewers exchange model elements, never reasoning:
a review task carries the changed elements and the items they answer, not the author's rationale.
Agents cannot approve, attest, declare tools or create baselines; those items are reported for a
person.
"""

from __future__ import annotations

import typing
from datetime import date
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field, ValidationError

from design_graph.agents.roles import DEFAULT_ROLES, FORBIDDEN_FOR_AGENTS, RoleSpec
from design_graph.model.elements import Principal, Role
from design_graph.model.engineering import Review
from design_graph.model.system import SystemModel, content_hash
from design_graph.model.validate import validate_model
from design_graph.process.rules import HUMAN, OpenItem
from design_graph.process.status import ProcessStatus, evaluate_process
from design_graph.process.templates import DEFAULT_TEMPLATE, ProcessTemplate

UNATTRIBUTED = "unattributed"


class Task(BaseModel):
    id: str
    round: int
    role: str
    author: str
    stage: str
    items: list[OpenItem]
    brief: str
    writes: list[str]
    rework_findings: list[str] = Field(default_factory=list)


class ReviewTask(BaseModel):
    id: str
    round: int
    reviewer: str
    author: str
    elements: dict[str, dict[str, Any]]
    items: list[OpenItem]
    checklist: list[str]


class Proposal(BaseModel):
    author: str
    upserts: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    rationale: str = ""


Outcome = Literal[
    "accepted",
    "rejected_permission",
    "rejected_validation",
    "rejected_review",
    "no_change",
    "error",
]


class TaskRecord(BaseModel):
    round: int
    task: str
    role: str
    author: str
    items: list[str]
    outcome: Outcome
    detail: str = ""
    changed: list[str] = Field(default_factory=list)
    review: Review | None = None


class OrchestrationReport(BaseModel):
    rounds: int
    stopped_because: str
    records: list[TaskRecord]
    status: ProcessStatus
    human_actions: list[OpenItem]


class AgentBackend(Protocol):
    """Whatever does the work: scripted functions, an LLM, or a person behind a form."""

    def execute(self, task: Task, model: SystemModel) -> Proposal: ...

    def review(self, task: ReviewTask, model: SystemModel) -> Review: ...


class Orchestrator:
    def __init__(
        self,
        model: SystemModel,
        backend: AgentBackend,
        *,
        template: ProcessTemplate = DEFAULT_TEMPLATE,
        roles: dict[str, RoleSpec] | None = None,
        principals: dict[str, str] | None = None,
        max_rounds: int = 5,
        max_attempts: int = 3,
        today: str | None = None,
    ) -> None:
        self.model = model.model_copy(deep=True)
        self.backend = backend
        self.template = template
        self.roles = roles or DEFAULT_ROLES
        self.principals = principals or {
            r.id: f"agent-{r.id}" for r in self.roles.values() if r.kind != "human"
        }
        self.max_rounds = max_rounds
        self.max_attempts = max_attempts
        self.attempts: dict[str, int] = {}
        self.today = today or str(date.today())
        self.authors: dict[str, str] = {}
        self.rework: dict[str, list[str]] = {}
        self._ensure_principals()

    # ------------------------------------------------------------------ setup

    def _ensure_principals(self) -> None:
        for role_id, pid in self.principals.items():
            role = self.roles[role_id]
            wanted: Role = "reviewer" if role.kind == "quality" else "author"
            existing = self.model.principal(pid)
            if existing is None:
                self.model.principals.append(
                    Principal(id=pid, name=role.title, kind="agent", roles=[wanted])
                )
            elif existing.kind != "agent":
                raise ValueError(f"principal {pid} for role {role_id} must be an agent")
        if self.model.principal(UNATTRIBUTED) is None:
            self.model.principals.append(
                Principal(
                    id=UNATTRIBUTED, name="Author not recorded", kind="agent", roles=["author"]
                )
            )

    @property
    def reviewer(self) -> str:
        quality = [r.id for r in self.roles.values() if r.kind == "quality"]
        if not quality:
            raise ValueError("no quality role defined")
        return self.principals[quality[0]]

    # ------------------------------------------------------------------ planning

    def plan(self, status: ProcessStatus | None = None, round_no: int = 1) -> list[Task]:
        """One task per owning role for the actionable blocking items; human items are skipped."""

        status = status or evaluate_process(self.model, self.template)
        by_owner: dict[str, list[OpenItem]] = {}
        for item in status.next_actions():
            if item.owner == HUMAN or item.owner not in self.roles:
                continue
            if self.roles[item.owner].kind == "human":
                continue
            if self.attempts.get(item.owner, 0) >= self.max_attempts:
                continue
            by_owner.setdefault(item.owner, []).append(item)
        tasks = []
        for owner, items in by_owner.items():
            role = self.roles[owner]
            tasks.append(
                Task(
                    id=f"T{round_no}-{owner}",
                    round=round_no,
                    role=owner,
                    author=self.principals[owner],
                    stage=items[0].stage,
                    items=items,
                    brief=self.brief(role, items, self.rework.get(owner, [])),
                    writes=role.writes,
                    rework_findings=self.rework.get(owner, []),
                )
            )
        return tasks

    @staticmethod
    def brief(role: RoleSpec, items: list[OpenItem], findings: list[str]) -> str:
        lines = [f"You are the {role.title}.", "", "Checklist:"]
        lines += [f"- {c}" for c in role.checklist]
        lines += ["", "Open items to resolve:"]
        lines += [
            f"- [{i.severity}] {i.rule} on {i.element}: {i.message} Action: {i.action}"
            for i in items
        ]
        if findings:
            lines += ["", "Your previous proposal was rejected. Findings to address:"]
            lines += [f"- {f}" for f in findings]
        lines += ["", f"You may write only these collections: {', '.join(role.writes) or 'none'}."]
        return "\n".join(lines)

    # ------------------------------------------------------------------ applying

    def apply(
        self, task: Task, proposal: Proposal
    ) -> tuple[SystemModel | None, Outcome, str, list[str]]:
        role = self.roles[task.role]
        if proposal.author != task.author:
            return None, "rejected_permission", f"author {proposal.author} is not {task.author}", []
        for collection, items in proposal.upserts.items():
            if collection in FORBIDDEN_FOR_AGENTS:
                return None, "rejected_permission", f"agents may not write {collection}", []
            if collection not in role.writes:
                return None, "rejected_permission", f"{role.id} may not write {collection}", []
            if collection == "evidence" and any(i.get("attestations") for i in items):
                return None, "rejected_permission", "agents may not attest evidence", []
        candidate = self.model.model_copy(deep=True)
        changed: list[str] = []
        for collection, items in proposal.upserts.items():
            field = SystemModel.model_fields.get(collection)
            if field is None:
                return None, "rejected_validation", f"unknown collection {collection}", []
            element_type = typing.get_args(field.annotation)[0]
            current = getattr(candidate, collection)
            for raw in items:
                try:
                    element = element_type.model_validate(raw)
                except ValidationError as error:
                    return (
                        None,
                        "rejected_validation",
                        f"{collection}: {error.errors()[0]['msg']}",
                        [],
                    )
                index = next((k for k, e in enumerate(current) if e.id == element.id), None)
                if index is None:
                    current.append(element)
                    changed.append(element.id)
                elif current[index] != element:
                    current[index] = element
                    changed.append(element.id)
        if not changed:
            return None, "no_change", "proposal changes nothing", []
        before = {(f.code, f.element) for f in validate_model(self.model) if f.severity == "error"}
        after = {(f.code, f.element) for f in validate_model(candidate) if f.severity == "error"}
        new_errors = sorted(after - before)
        if new_errors:
            detail = "; ".join(f"{code} on {element}" for code, element in new_errors)
            return None, "rejected_validation", f"new validation errors: {detail}", changed
        return candidate, "accepted", "", changed

    def review_task(
        self,
        task_id: str,
        round_no: int,
        candidate: SystemModel,
        changed: list[str],
        author: str,
        items: list[OpenItem],
    ) -> ReviewTask:
        quality = next(r for r in self.roles.values() if r.kind == "quality")
        elements: dict[str, dict[str, Any]] = {}
        for eid in changed:
            element = candidate.get(eid)
            if element is not None:
                elements[eid] = {"type": type(element).__name__, **element.model_dump(mode="json")}
        return ReviewTask(
            id=f"R-{task_id}",
            round=round_no,
            reviewer=self.reviewer,
            author=author,
            elements=elements,
            items=items,
            checklist=quality.checklist,
        )

    def _record_review(
        self, review: Review, task: ReviewTask, candidate: SystemModel, changed: list[str]
    ) -> Review:
        """Pin the review to the reviewed content; enforce independence whatever the backend did."""

        if review.reviewer != task.reviewer:
            raise ValueError(f"review must be by {task.reviewer}, got {review.reviewer}")
        return review.model_copy(
            update={
                "id": f"{task.id}-{len(candidate.reviews) + 1}",
                "author": task.author,
                "elements": changed,
                "content_hashes": {e: content_hash(candidate, e) for e in changed},
                "date": self.today,
            }
        )

    # ------------------------------------------------------------------ loop

    def review_existing(self, items: list[OpenItem], round_no: int) -> list[TaskRecord]:
        """Review elements that need a review but were not produced by this orchestrator."""

        records = []
        ids = [i.element for i in items if self.model.get(i.element) is not None]
        by_author: dict[str, list[str]] = {}
        for eid in ids:
            by_author.setdefault(self.authors.get(eid, UNATTRIBUTED), []).append(eid)
        for author, eids in by_author.items():
            task = self.review_task(
                f"T{round_no}-review-{author}",
                round_no,
                self.model,
                eids,
                author,
                [i for i in items if i.element in eids],
            )
            review = self._record_review(
                self.backend.review(task, self.model), task, self.model, eids
            )
            self.model.reviews.append(review)
            records.append(
                TaskRecord(
                    round=round_no,
                    task=task.id,
                    role="quality_reviewer",
                    author=author,
                    items=[i.id for i in task.items],
                    outcome="accepted" if review.accepted else "rejected_review",
                    changed=eids,
                    review=review,
                )
            )
        return records

    def _retry(self, role: str) -> bool:
        """Count a rejected attempt; True while the role may try again with the feedback."""

        self.attempts[role] = self.attempts.get(role, 0) + 1
        return self.attempts[role] < self.max_attempts

    def run(self) -> OrchestrationReport:
        records: list[TaskRecord] = []
        stopped = "max rounds reached"
        round_no = 0
        for round_no in range(1, self.max_rounds + 1):
            status = evaluate_process(self.model, self.template)
            tasks = self.plan(status, round_no)
            if not tasks:
                stopped = "no open items an agent can act on"
                break
            progress = False
            for task in tasks:
                if self.roles[task.role].kind == "quality":
                    new = self.review_existing(task.items, round_no)
                    records += new
                    progress = progress or any(r.outcome == "accepted" for r in new)
                    continue
                try:
                    proposal = self.backend.execute(task, self.model.model_copy(deep=True))
                except Exception as error:  # noqa: BLE001 - a failing agent must not stop the loop
                    records.append(
                        TaskRecord(
                            round=round_no,
                            task=task.id,
                            role=task.role,
                            author=task.author,
                            items=[i.id for i in task.items],
                            outcome="error",
                            detail=repr(error),
                        )
                    )
                    continue
                candidate, outcome, detail, changed = self.apply(task, proposal)
                record = TaskRecord(
                    round=round_no,
                    task=task.id,
                    role=task.role,
                    author=task.author,
                    items=[i.id for i in task.items],
                    outcome=outcome,
                    detail=detail,
                    changed=changed,
                )
                if candidate is None:
                    if outcome != "no_change":
                        self.rework[task.role] = [detail]
                        progress = self._retry(task.role) or progress
                    records.append(record)
                    continue
                review_task = self.review_task(
                    task.id, round_no, candidate, changed, task.author, task.items
                )
                review = self._record_review(
                    self.backend.review(review_task, candidate), review_task, candidate, changed
                )
                record.review = review
                if review.accepted:
                    candidate.reviews.append(review)
                    self.model = candidate
                    for eid in changed:
                        self.authors[eid] = task.author
                    self.rework.pop(task.role, None)
                    self.attempts.pop(task.role, None)
                    progress = True
                else:
                    record.outcome = "rejected_review"
                    self.rework[task.role] = [
                        f"[{f.severity}] {f.text}"
                        for f in review.findings
                        if f.severity in ("blocking", "major") and not f.resolved
                    ]
                    progress = self._retry(task.role) or progress
                records.append(record)
            if not progress:
                stopped = "no progress in a round"
                break
        status = evaluate_process(self.model, self.template)
        return OrchestrationReport(
            rounds=round_no,
            stopped_because=stopped,
            records=records,
            status=status,
            human_actions=status.human_actions,
        )
