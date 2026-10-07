# SPDX-License-Identifier: Apache-2.0
"""Synthetic requirement cards, fidelity checks, and sourced input binding."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from design_graph.core import Sourced

RequirementKind = Literal[
    "frequency_max_Hz",
    "frequency_min_Hz",
    "envelope_max_mm",
    "envelope_min_mm",
    "random_asd",
    "qsl_min_g",
    "load_case",
]
RequirementMethod = Literal["A", "R", "I", "T"]
RequirementStatus = Literal["draft", "active", "withdrawn"]

_QUANTITY_UNITS = {
    "frequency_max_Hz": ("Hz", "kHz", "MHz", "GHz"),
    "frequency_min_Hz": ("Hz", "kHz", "MHz", "GHz"),
    "envelope_max_mm": ("mm", "cm", "m"),
    "envelope_min_mm": ("mm", "cm", "m"),
    "random_asd": ("g^2/Hz", "g²/Hz"),
    "qsl_min_g": ("g", "m/s^2", "m/s²"),
}
_UNIT_SCALE = {
    "Hz": 1.0,
    "kHz": 1_000.0,
    "MHz": 1_000_000.0,
    "GHz": 1_000_000_000.0,
    "mm": 1.0,
    "cm": 10.0,
    "m": 1_000.0,
    "g^2/Hz": 1.0,
    "g²/Hz": 1.0,
    "g": 1.0,
    "m/s^2": 1.0 / 9.80665,
    "m/s²": 1.0 / 9.80665,
}
_NUMBER_WITH_UNIT = re.compile(r"(?<![A-Za-z0-9_.])([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*([A-Za-z²^/]+)")


class RequirementCard(BaseModel):
    """A typed, synthetic requirement record."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: RequirementKind
    params: dict[str, float | str]
    quote: str
    method: RequirementMethod
    tbc: bool = False
    status: RequirementStatus = "active"

    @model_validator(mode="after")
    def check_card_shape(self) -> RequirementCard:
        if not self.id.strip():
            raise ValueError("requirement id must be non-empty")
        if not self.quote.strip():
            raise ValueError("requirement quote must be non-empty")
        if self.kind == "load_case":
            if not isinstance(self.params.get("name"), str):
                raise ValueError("load_case requires a string 'name' parameter")
        elif not any(isinstance(value, (int, float)) for value in self.params.values()):
            raise ValueError(f"{self.kind} requires a numeric parameter")
        return self


class DesignInputs(BaseModel):
    """Requirement-derived engineering drivers with preserved source IDs."""

    f_max_Hz: Sourced[float] | None = None
    f_min_Hz: Sourced[float] | None = None
    envelope_max_mm: Sourced[float] | None = None
    envelope_min_mm: Sourced[float] | None = None
    random_asd: Sourced[float] | None = None
    qsl_g: Sourced[float] | None = None
    load_cases: list[Sourced[str]] = Field(default_factory=list)


class RequirementConflict(BaseModel):
    """A pair of incompatible active limits requiring a human decision."""

    kind: str
    requirement_ids: list[str]
    values: list[float]
    verdict: Literal["DECIDE"] = "DECIDE"
    reason: str


def check_fidelity(card: RequirementCard) -> list[str]:
    """Return card issues when its numeric values or units are absent from its quote."""

    if card.kind == "load_case":
        return [] if str(card.params["name"]).casefold() in card.quote.casefold() else [
            f"{card.id}: load-case name is absent from quote"
        ]
    canonical_units = _QUANTITY_UNITS[card.kind]
    quote_quantities = [
        (float(number), unit) for number, unit in _NUMBER_WITH_UNIT.findall(card.quote)
    ]
    issues: list[str] = []
    numeric_values = [float(value) for value in card.params.values() if isinstance(value, (int, float))]
    if not quote_quantities:
        return [f"{card.id}: quote contains no number with a parseable unit"]
    for expected in numeric_values:
        matched = any(
            unit in canonical_units
            and abs(number * _UNIT_SCALE[unit] - expected) <= max(abs(expected), 1.0) * 1e-9
            for number, unit in quote_quantities
        )
        if not matched:
            issues.append(f"{card.id}: numeric parameter {expected:g} with expected units is absent from quote")
    invalid_units = [unit for _, unit in quote_quantities if unit not in canonical_units]
    if invalid_units:
        issues.append(f"{card.id}: unsupported unit {invalid_units[0]!r} for {card.kind}")
    return issues


def bind_requirements(cards: list[RequirementCard]) -> tuple[DesignInputs, list[RequirementConflict]]:
    """Bind active cards using strictest limits and retain every load case."""

    issues = [issue for card in cards for issue in check_fidelity(card)]
    if issues:
        raise ValueError("; ".join(issues))
    active = [card for card in cards if card.status == "active"]
    groups: dict[str, list[tuple[RequirementCard, float]]] = {}
    for card in active:
        if card.kind == "load_case":
            continue
        magnitude = next(float(value) for value in card.params.values() if isinstance(value, (int, float)))
        groups.setdefault(card.kind, []).append((card, magnitude))

    conflicts: list[RequirementConflict] = []
    selected: dict[str, Sourced[float]] = {}
    for kind, entries in groups.items():
        values = [value for _, value in entries]
        # Maximum constraints choose the smallest ceiling; minimum constraints choose the largest floor.
        chosen_value = max(values) if kind in {
            "qsl_min_g", "frequency_min_Hz", "envelope_min_mm"
        } else min(values)
        chosen = [card for card, value in entries if value == chosen_value]
        output_key = {
            "frequency_max_Hz": "f_max_Hz",
            "frequency_min_Hz": "f_min_Hz",
            "qsl_min_g": "qsl_g",
        }.get(kind, kind)
        selected[output_key] = Sourced[float](
            value=chosen_value,
            sources=[card.id for card in chosen],
            provisional=["[P] requirement is marked TBC"] if any(card.tbc for card in chosen) else [],
        )

    for quantity, upper_key, lower_key in (
        ("frequency_Hz", "f_max_Hz", "f_min_Hz"),
        ("envelope_mm", "envelope_max_mm", "envelope_min_mm"),
    ):
        upper = selected.get(upper_key)
        lower = selected.get(lower_key)
        if upper is not None and lower is not None and lower.value > upper.value:
            conflicts.append(
                RequirementConflict(
                    kind=quantity,
                    requirement_ids=[
                        card.id for card in active if card.id in set(lower.sources + upper.sources)
                    ],
                    values=[lower.value, upper.value],
                    reason="The active minimum exceeds the active maximum.",
                )
            )
            selected.pop(upper_key)
            selected.pop(lower_key)

    load_cases = [
        Sourced[str](
            value=str(card.params["name"]),
            sources=[card.id],
            provisional=["[P] requirement is marked TBC"] if card.tbc else [],
        )
        for card in active
        if card.kind == "load_case"
    ]
    return DesignInputs(**selected, load_cases=load_cases), conflicts