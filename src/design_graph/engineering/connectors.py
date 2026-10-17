# SPDX-License-Identifier: Apache-2.0
"""Deterministic connector wall assignment with EMC priority and route cost."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from design_graph.core import rule


class Connector(BaseModel):
    part: str
    group: str
    contacts: int
    destination: str
    emc_class: Literal["critical", "high", "normal"]
    footprint_mm: float
    wire_length_by_wall_mm: dict[str, float]
    route_penalty_by_wall: dict[str, float] = Field(default_factory=dict)


class ConnectorPlacement(BaseModel):
    group: str
    wall: str
    connectors: list[str]
    emc_class: str
    cost: float
    cost_terms: dict[str, float]


@rule(version="1", source="destination mapping and EMC-prioritized wall cost search")
def route_connectors(
    connectors: list[Connector],
    walls: list[str],
    destination_to_wall: dict[str, str],
    destination_mismatch_penalty: float = 100.0,
) -> list[ConnectorPlacement]:
    """Assign each connector group to its lowest-cost wall, evaluating critical classes first."""

    if not walls:
        raise ValueError("at least one candidate wall is required")
    if destination_mismatch_penalty < 0:
        raise ValueError("destination mismatch penalty cannot be negative")
    grouped: dict[str, list[Connector]] = {}
    for connector in connectors:
        if connector.contacts <= 0 or connector.footprint_mm < 0:
            raise ValueError(f"invalid connector dimensions for {connector.part!r}")
        if set(walls) - set(connector.wire_length_by_wall_mm):
            raise ValueError(
                f"connector {connector.part!r} lacks wire lengths for a candidate wall"
            )
        grouped.setdefault(connector.group, []).append(connector)
    priority = {"critical": 0, "high": 1, "normal": 2}
    ordered_groups = sorted(
        grouped.items(),
        key=lambda entry: (min(priority[item.emc_class] for item in entry[1]), entry[0]),
    )
    placements: list[ConnectorPlacement] = []
    for group_name, members in ordered_groups:
        wall_costs: list[tuple[float, str, dict[str, float]]] = []
        for wall in walls:
            footprint = sum(item.footprint_mm for item in members)
            wire = sum(item.wire_length_by_wall_mm[wall] for item in members)
            route = sum(item.route_penalty_by_wall.get(wall, 0.0) for item in members)
            mismatch = sum(
                destination_mismatch_penalty
                for item in members
                if destination_to_wall.get(item.destination) not in (None, wall)
            )
            terms = {
                "footprint_mm": footprint,
                "wire_length_mm": wire,
                "route_penalty": route,
                "destination_mismatch": mismatch,
            }
            wall_costs.append((sum(terms.values()), wall, terms))
        cost, wall, terms = min(wall_costs, key=lambda item: (item[0], walls.index(item[1])))
        classes = sorted(
            {item.emc_class for item in members},
            key=lambda emc_class: priority[emc_class],
        )
        placements.append(
            ConnectorPlacement(
                group=group_name,
                wall=wall,
                connectors=[
                    item.part
                    for item in sorted(
                        members, key=lambda item: (priority[item.emc_class], item.part)
                    )
                ],
                emc_class=classes[0],
                cost=cost,
                cost_terms=terms,
            )
        )
    return placements
