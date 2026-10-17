# SPDX-License-Identifier: Apache-2.0
"""Stage status and next actions, computed from the model and the open items."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from design_graph.model.system import SystemModel
from design_graph.process.rules import HUMAN, OpenItem, open_items
from design_graph.process.templates import DEFAULT_TEMPLATE, ProcessTemplate

StageState = Literal["complete", "in_progress", "blocked", "not_started"]


class StageStatus(BaseModel):
    id: str
    name: str
    state: StageState
    missing: list[str]
    blocking: int
    warnings: int
    human_blocking: int


class ProcessStatus(BaseModel):
    template: str
    stages: list[StageStatus]
    items: list[OpenItem]

    def next_actions(
        self, owner: str | None = None, include_warnings: bool = False
    ) -> list[OpenItem]:
        """Open items in work order: earliest unfinished stage first, blocking before warnings.
        Only stages whose dependencies are complete are actionable."""

        actionable = {s.id for s in self.stages if s.state in ("in_progress", "not_started")}
        order = {s.id: i for i, s in enumerate(self.stages)}
        picked = [
            i
            for i in self.items
            if i.stage in actionable
            and (include_warnings or i.severity == "blocking")
            and (owner is None or i.owner == owner)
        ]
        return sorted(
            picked, key=lambda i: (order.get(i.stage, 99), i.severity != "blocking", i.rule)
        )

    @property
    def human_actions(self) -> list[OpenItem]:
        return [i for i in self.items if i.owner == HUMAN and i.severity == "blocking"]

    @property
    def complete(self) -> bool:
        return all(s.state == "complete" for s in self.stages)


def evaluate_process(
    model: SystemModel, template: ProcessTemplate = DEFAULT_TEMPLATE
) -> ProcessStatus:
    problems = template.check()
    if problems:
        raise ValueError(f"invalid process template: {problems}")
    items = open_items(model)
    states: dict[str, StageState] = {}
    stages = []
    for stage in template.stages:
        missing = [name for name in stage.requires if not getattr(model, name, [])]
        mine = [i for i in items if i.stage == stage.id]
        blocking = [i for i in mine if i.severity == "blocking"]
        if any(states.get(d) != "complete" for d in stage.depends_on):
            state: StageState = "blocked"
        elif len(missing) == len(stage.requires) and stage.requires and not mine:
            state = "not_started"
        elif missing or blocking:
            state = "in_progress"
        else:
            state = "complete"
        states[stage.id] = state
        stages.append(
            StageStatus(
                id=stage.id,
                name=stage.name,
                state=state,
                missing=missing,
                blocking=len(blocking),
                warnings=len(mine) - len(blocking),
                human_blocking=sum(1 for i in blocking if i.owner == HUMAN),
            )
        )
    return ProcessStatus(template=template.id, stages=stages, items=items)
