# SPDX-License-Identifier: Apache-2.0
"""Bounded, trial-evaluated sizing controller for numeric graph bindings."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from design_graph.core import Graph, GraphValidationError


class Lever(BaseModel):
    name: str
    node: str
    binding: str
    step: float
    reach: float
    reason: str


class ControllerAction(BaseModel):
    lever: str
    node: str
    binding: str
    reason: str
    outcome: Literal["accepted", "tried, reverted", "advice only", "no change"]
    margin_before: float
    margin_after: float | None
    binding_before: Any
    binding_after: Any
    advice: str | None = None


class SizingController:
    """Trial one declared binding change, retaining it only when margin improves."""

    def __init__(
        self,
        graph: Graph,
        *,
        target_node: str,
        margin_path: str,
        lever: Lever,
        improvement_threshold: float = 0.01,
    ) -> None:
        self.graph = graph
        self.target_node = target_node
        self.margin_path = margin_path
        self.lever = lever
        self.improvement_threshold = improvement_threshold
        if improvement_threshold < 0:
            raise ValueError("improvement threshold cannot be negative")
        if lever.reach <= 0:
            raise ValueError("lever reach must be positive")

    def run(self) -> list[ControllerAction]:
        current_value = self.graph.evaluate(self.target_node)[self.target_node]
        margin_before = _read_path(current_value, self.margin_path)
        binding_before = self.graph.binding(self.lever.node, self.lever.binding)
        if margin_before >= 0:
            return [
                ControllerAction(
                    lever=self.lever.name,
                    node=self.lever.node,
                    binding=self.lever.binding,
                    reason=self.lever.reason,
                    outcome="no change",
                    margin_before=margin_before,
                    margin_after=margin_before,
                    binding_before=binding_before,
                    binding_after=binding_before,
                )
            ]
        gap = -margin_before
        if gap > self.lever.reach:
            return [
                ControllerAction(
                    lever=self.lever.name,
                    node=self.lever.node,
                    binding=self.lever.binding,
                    reason=self.lever.reason,
                    outcome="advice only",
                    margin_before=margin_before,
                    margin_after=None,
                    binding_before=binding_before,
                    binding_after=binding_before,
                    advice=(
                        f"Gap {gap:g} exceeds declared reach {self.lever.reach:g}; "
                        "review another design lever."
                    ),
                )
            ]
        if not isinstance(binding_before, (float, int)):
            raise GraphValidationError("sizing lever binding must resolve to a numeric constant")
        binding_trial = binding_before + self.lever.step
        try:
            self.graph.set(self.lever.node, **{self.lever.binding: binding_trial})
            trial_value = self.graph.evaluate(self.target_node)[self.target_node]
            margin_trial = _read_path(trial_value, self.margin_path)
        except Exception:
            self.graph.set(self.lever.node, **{self.lever.binding: binding_before})
            self.graph.evaluate(self.target_node)
            raise
        if margin_trial - margin_before >= self.improvement_threshold:
            return [
                ControllerAction(
                    lever=self.lever.name,
                    node=self.lever.node,
                    binding=self.lever.binding,
                    reason=self.lever.reason,
                    outcome="accepted",
                    margin_before=margin_before,
                    margin_after=margin_trial,
                    binding_before=binding_before,
                    binding_after=binding_trial,
                )
            ]
        self.graph.set(self.lever.node, **{self.lever.binding: binding_before})
        self.graph.evaluate(self.target_node)
        return [
            ControllerAction(
                lever=self.lever.name,
                node=self.lever.node,
                binding=self.lever.binding,
                reason=self.lever.reason,
                outcome="tried, reverted",
                margin_before=margin_before,
                margin_after=margin_trial,
                binding_before=binding_before,
                binding_after=binding_before,
            )
        ]


def _read_path(value: Any, path: str) -> float:
    for part in path.split(".") if path else []:
        value = getattr(value, part) if hasattr(value, part) else value[part]
    if not isinstance(value, (int, float)):
        raise GraphValidationError(f"target path {path!r} did not resolve to a number")
    return float(value)
