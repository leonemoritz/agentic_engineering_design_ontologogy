# SPDX-License-Identifier: Apache-2.0
"""Requirement cards: the import record for requirements, with a quote-fidelity check."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator

from design_graph.model.units import ureg

RequirementMethod = Literal["A", "R", "I", "T"]
RequirementStatus = Literal["draft", "active", "withdrawn"]
CardRelation = Literal["le", "ge", "eq", "range"]

_NUMBER_WITH_UNIT = re.compile(
    r"(?<![A-Za-z0-9_.])([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*([A-Za-z°µ²³^/]*)"
)


class RequirementCard(BaseModel):
    """One requirement as written in a source document.

    A card is quantitative when it has a relation: `attribute relation value [unit]`, with
    `upper` for a range and `tolerance` for `eq`. Without a relation it is qualitative and only
    its quote matters.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    quote: str
    method: RequirementMethod
    attribute: str | None = None
    relation: CardRelation | None = None
    value: float | None = None
    upper: float | None = None
    tolerance: float | None = None
    unit: str = ""
    tbc: bool = False
    status: RequirementStatus = "active"

    @model_validator(mode="after")
    def check_card_shape(self) -> RequirementCard:
        if not self.id.strip():
            raise ValueError("requirement id must be non-empty")
        if not self.quote.strip():
            raise ValueError("requirement quote must be non-empty")
        if self.relation is None:
            if self.attribute is not None or self.value is not None:
                raise ValueError("attribute and value need a relation")
            return self
        if not self.attribute or self.value is None:
            raise ValueError(f"relation {self.relation} needs an attribute and a value")
        if self.relation == "range" and self.upper is None:
            raise ValueError("range needs an upper value")
        if self.relation == "eq" and self.tolerance is None:
            raise ValueError("eq needs a tolerance")
        try:
            ureg.Quantity(1.0, self.unit)
        except Exception as error:
            raise ValueError(f"unknown unit {self.unit!r}") from error
        return self

    def numbers(self) -> list[float]:
        """The numeric values the quote must contain."""

        return [v for v in (self.value, self.upper, self.tolerance) if v is not None]


def check_fidelity(card: RequirementCard) -> list[str]:
    """Return issues when a quantitative card's numbers are absent from its quote.

    A number in the quote matches when, read with the unit written after it (or as dimensionless
    when none is), it converts to the card's value in the card's unit.
    """

    if card.relation is None:
        return []
    found = [_read(number, unit) for number, unit in _NUMBER_WITH_UNIT.findall(card.quote)]
    if not found:
        return [f"{card.id}: quote contains no number"]
    issues: list[str] = []
    for expected in card.numbers():
        if not any(_matches(quantity, expected, card.unit) for quantity in found):
            unit = f" {card.unit}" if card.unit else ""
            issues.append(f"{card.id}: {expected:g}{unit} is absent from the quote")
    return issues


def _read(number: str, unit: str) -> Any:
    try:
        return ureg.Quantity(float(number), unit)
    except Exception:
        return ureg.Quantity(float(number), "")


def _matches(quantity: Any, expected: float, unit: str) -> bool:
    try:
        value = float(quantity.to(unit).magnitude)
    except Exception:
        return False
    return abs(value - expected) <= max(abs(expected), 1.0) * 1e-9
