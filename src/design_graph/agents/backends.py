# SPDX-License-Identifier: Apache-2.0
"""Backends that do the work for the orchestrator.

`CallableBackend` runs plain Python functions (scripted agents, tests, deterministic tools).
`PromptBackend` turns tasks into prompts for any language model and parses its JSON answer; pass a
`complete(system, user) -> str` function that calls the model of your choice. No model SDK is a
dependency of this package.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from typing import Any

from design_graph.agents.orchestrator import Proposal, ReviewTask, Task
from design_graph.model.engineering import Review, ReviewFinding
from design_graph.model.system import SystemModel

Executor = Callable[[Task, SystemModel], Proposal]
Reviewer = Callable[[ReviewTask, SystemModel], Review]
Complete = Callable[[str, str], str]


class CallableBackend:
    def __init__(self, executors: Mapping[str, Executor], reviewer: Reviewer) -> None:
        self.executors = dict(executors)
        self.reviewer = reviewer

    def execute(self, task: Task, model: SystemModel) -> Proposal:
        if task.role not in self.executors:
            raise KeyError(f"no executor for role {task.role}")
        return self.executors[task.role](task, model)

    def review(self, task: ReviewTask, model: SystemModel) -> Review:
        return self.reviewer(task, model)


AUTHOR_SYSTEM = """You are one role in an engineering team. The shared state is a system model; you
change it only by proposing elements. Answer with one JSON object and nothing else:
{"upserts": {"<collection>": [<element>, ...]}, "rationale": "<short>"}
Each element is a full object for that collection, with an "id". Use only the collections you are
allowed to write. Never invent measured values: analysis results come from tools, tests from
people."""

REVIEW_SYSTEM = """You are an independent quality reviewer. You see the changed elements and the
items they were meant to resolve, not the author's reasoning. Try to show the elements are wrong,
incomplete or inconsistent with the requirements. Answer with one JSON object and nothing else:
{"checked": ["<what you checked>", ...],
 "findings": [{"severity": "blocking|major|minor|note", "text": "...", "element": "<id or null>"}]}
An empty findings list means you found nothing. You cannot approve anything."""


def _json_object(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text, re.S)
    if match is None:
        raise ValueError("model answer contains no JSON object")
    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("model answer is not a JSON object")
    return data


def model_digest(model: SystemModel, limit: int = 60) -> str:
    """A compact view of the model for prompts: ids and one-line text per element."""

    lines = []
    for name, value in model:
        if not isinstance(value, list) or not value:
            continue
        lines.append(f"{name}:")
        for element in value[:limit]:
            text = (
                getattr(element, "text", "")
                or getattr(element, "description", "")
                or getattr(element, "name", "")
                or getattr(element, "statement", "")
            )
            lines.append(f"  - {element.id}: {str(text)[:120]}")
    return "\n".join(lines)


class PromptBackend:
    def __init__(self, complete: Complete, *, digest_limit: int = 60) -> None:
        self.complete = complete
        self.digest_limit = digest_limit

    def author_prompt(self, task: Task, model: SystemModel) -> str:
        return f"{task.brief}\n\nCurrent model:\n{model_digest(model, self.digest_limit)}\n"

    def review_prompt(self, task: ReviewTask, model: SystemModel) -> str:
        items = "\n".join(f"- {i.rule} on {i.element}: {i.message}" for i in task.items)
        checklist = "\n".join(f"- {c}" for c in task.checklist)
        elements = json.dumps(task.elements, indent=1)
        return (
            f"Checklist:\n{checklist}\n\nItems the author was asked to resolve:\n{items}\n\n"
            f"Changed elements:\n{elements}\n\nModel context:\n"
            f"{model_digest(model, self.digest_limit)}\n"
        )

    def execute(self, task: Task, model: SystemModel) -> Proposal:
        data = _json_object(self.complete(AUTHOR_SYSTEM, self.author_prompt(task, model)))
        return Proposal(
            author=task.author,
            upserts=data.get("upserts", {}),
            rationale=str(data.get("rationale", "")),
        )

    def review(self, task: ReviewTask, model: SystemModel) -> Review:
        data = _json_object(self.complete(REVIEW_SYSTEM, self.review_prompt(task, model)))
        findings = [ReviewFinding.model_validate(f) for f in data.get("findings", [])]
        return Review(
            id=f"{task.id}-draft",
            elements=list(task.elements),
            reviewer=task.reviewer,
            author=task.author,
            date="2000-01-01",
            checked=[str(c) for c in data.get("checked", [])],
            findings=findings,
        )
