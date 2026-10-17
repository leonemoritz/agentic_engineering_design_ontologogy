# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json

from proc_helpers import add_concepts, bracket_model, trade_study

from design_graph.agents import (
    CallableBackend,
    Orchestrator,
    PromptBackend,
    Proposal,
    ReviewTask,
    Task,
)
from design_graph.model import SystemModel
from design_graph.model.engineering import Review, ReviewFinding

SECRET = "my private chain of thought"


def concepts_proposal(task: Task, model: SystemModel) -> Proposal:
    return Proposal(
        author=task.author,
        rationale=SECRET,
        upserts={
            "concepts": [{"id": "CA"}, {"id": "CB"}],
            "trade_studies": [trade_study().model_dump(mode="json")],
        },
    )


def accept(task: ReviewTask, model: SystemModel) -> Review:
    return Review(
        id="draft",
        elements=list(task.elements),
        reviewer=task.reviewer,
        author=task.author,
        date="2000-01-01",
        checked=["scores vs decision"],
    )


class RejectOnce:
    def __init__(self) -> None:
        self.calls = 0
        self.seen: list[ReviewTask] = []

    def __call__(self, task: ReviewTask, model: SystemModel) -> Review:
        self.calls += 1
        self.seen.append(task)
        findings = (
            [] if self.calls > 1 else [ReviewFinding(severity="major", text="Add a third concept")]
        )
        return Review(
            id="draft",
            elements=list(task.elements),
            reviewer=task.reviewer,
            author=task.author,
            date="2000-01-01",
            findings=findings,
        )


def test_plan_routes_items_to_roles_and_skips_people() -> None:
    orch = Orchestrator(bracket_model(), CallableBackend({}, accept))
    tasks = orch.plan()
    assert [t.role for t in tasks] == ["concept_engineer"]
    assert "List at least three genuinely different concepts" in tasks[0].brief
    assert "approvals" not in tasks[0].writes


def test_accepted_change_is_reviewed_independently_and_pinned() -> None:
    orch = Orchestrator(
        bracket_model(),
        CallableBackend({"concept_engineer": concepts_proposal}, accept),
        max_rounds=2,
        today="2026-02-02",
    )
    report = orch.run()
    accepted = [r for r in report.records if r.outcome == "accepted"]
    assert accepted and accepted[0].changed == ["CA", "CB", "TS1"]
    rev = orch.model.reviews[0]
    assert rev.reviewer == "agent-quality_reviewer" and rev.author == "agent-concept_engineer"
    assert set(rev.content_hashes) == {"CA", "CB", "TS1"} and rev.date == "2026-02-02"
    assert {s.id: s.state for s in report.status.stages}["concepts"] == "complete"
    assert {i.element for i in report.human_actions} == {"R1", "C1"}


def test_rejected_review_sends_findings_back_and_reviewer_never_sees_rationale() -> None:
    reviewer = RejectOnce()
    briefs: list[str] = []

    def author(task: Task, model: SystemModel) -> Proposal:
        briefs.append(task.brief)
        return concepts_proposal(task, model)

    orch = Orchestrator(
        bracket_model(), CallableBackend({"concept_engineer": author}, reviewer), max_rounds=3
    )
    report = orch.run()
    outcomes = [r.outcome for r in report.records if r.role == "concept_engineer"]
    assert outcomes[:2] == ["rejected_review", "accepted"]
    assert "Add a third concept" in briefs[1]
    assert all(SECRET not in json.dumps(t.model_dump(mode="json")) for t in reviewer.seen)


def test_agents_cannot_write_approvals_or_outside_their_role() -> None:
    def sneaky(task: Task, model: SystemModel) -> Proposal:
        return Proposal(
            author=task.author,
            upserts={
                "approvals": [
                    {
                        "id": "A1",
                        "element": "R1",
                        "content_hash": "x",
                        "principal": "eng",
                        "date": "2026-01-01",
                    }
                ]
            },
        )

    def wrong_collection(task: Task, model: SystemModel) -> Proposal:
        return Proposal(author=task.author, upserts={"allowables": []})

    for executor, word in ((sneaky, "approvals"), (wrong_collection, "allowables")):
        orch = Orchestrator(
            bracket_model(), CallableBackend({"concept_engineer": executor}, accept), max_rounds=1
        )
        record = orch.run().records[0]
        assert record.outcome == "rejected_permission" and word in record.detail
        assert not orch.model.approvals and not orch.model.trade_studies


def test_proposal_that_breaks_validation_is_not_applied() -> None:
    def dangling(task: Task, model: SystemModel) -> Proposal:
        bad = trade_study().model_dump(mode="json")
        bad["concepts"] = ["CA", "CZ"]
        return Proposal(
            author=task.author, upserts={"concepts": [{"id": "CA"}], "trade_studies": [bad]}
        )

    orch = Orchestrator(
        bracket_model(), CallableBackend({"concept_engineer": dangling}, accept), max_rounds=1
    )
    record = orch.run().records[0]
    assert record.outcome == "rejected_validation" and "ENG_DANGLING" in record.detail
    assert not orch.model.trade_studies


def test_quality_role_reviews_existing_unreviewed_elements() -> None:
    model = bracket_model()
    add_concepts(model)
    orch = Orchestrator(model, CallableBackend({}, accept), max_rounds=1)
    report = orch.run()
    assert report.records[0].role == "quality_reviewer"
    assert orch.model.reviews[0].author == "unattributed"


def test_prompt_backend_parses_answers_and_hides_rationale() -> None:
    prompts: list[tuple[str, str]] = []

    def complete(system: str, user: str) -> str:
        prompts.append((system, user))
        if "independent quality reviewer" in system:
            return 'Sure.\n{"checked": ["decision vs scores"], "findings": []}'
        body = {
            "upserts": {
                "concepts": [{"id": "CA"}, {"id": "CB"}],
                "trade_studies": [trade_study().model_dump(mode="json")],
            },
            "rationale": SECRET,
        }
        return json.dumps(body)

    orch = Orchestrator(bracket_model(), PromptBackend(complete), max_rounds=1)
    report = orch.run()
    assert report.records[0].outcome == "accepted"
    review_prompts = [u for s, u in prompts if "independent quality reviewer" in s]
    assert review_prompts and SECRET not in review_prompts[0]
    assert orch.model.reviews[0].checked == ["decision vs scores"]


def test_rework_stops_after_max_attempts() -> None:
    def always_reject(task: ReviewTask, model: SystemModel) -> Review:
        return Review(
            id="draft",
            elements=list(task.elements),
            reviewer=task.reviewer,
            author=task.author,
            date="2000-01-01",
            findings=[ReviewFinding(severity="blocking", text="Still wrong")],
        )

    orch = Orchestrator(
        bracket_model(),
        CallableBackend({"concept_engineer": concepts_proposal}, always_reject),
        max_rounds=10,
        max_attempts=2,
    )
    report = orch.run()
    attempts = [r for r in report.records if r.role == "concept_engineer"]
    assert len(attempts) == 2 and all(r.outcome == "rejected_review" for r in attempts)
    assert report.stopped_because in ("no progress in a round", "no open items an agent can act on")
    assert not orch.model.trade_studies
