# SPDX-License-Identifier: Apache-2.0
"""Typed verification rows and replayable fidelity-tier decisions."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Verdict(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    OPEN = "OPEN"
    DECIDE = "DECIDE"
    DELIVERABLE = "DELIVERABLE"
    NEEDS_HIGHER_TIER = "NEEDS-HIGHER-TIER"


class Evidence(BaseModel):
    node: str
    path: str = ""


class VerificationRow(BaseModel):
    req_ids: list[str]
    topic: str
    requirement: str
    method: Literal["A", "R", "I", "T"]
    evidence: Evidence
    value: Any
    margin: float | None
    verdict: Literal["PASS", "FAIL", "OPEN", "DECIDE", "DELIVERABLE"]
    provisional: bool = False
    provisional_reasons: list[str] = Field(default_factory=list)


class TierValidity(BaseModel):
    minimum: float | None = None
    maximum: float | None = None
    uncertainty: float = 0.0


class DecisionRecord(BaseModel):
    item: str
    tier: str
    relation: Literal["max", "min"]
    used_numbers: dict[str, float | bool | None]
    verdict: Literal["PASS", "FAIL", "OPEN", "NEEDS-HIGHER-TIER"]
    margin: float | None
    reason: str


def decide(
    *,
    item: str,
    tier: str,
    value: float,
    limit: float,
    relation: Literal["max", "min"],
    validity: TierValidity,
    resolved: bool = True,
    higher_tier_enabled: bool = False,
) -> DecisionRecord:
    """Evaluate one result only when it is resolved and inside its declared validity."""

    in_range = (validity.minimum is None or value >= validity.minimum) and (
        validity.maximum is None or value <= validity.maximum
    )
    numbers: dict[str, float | bool | None] = {
        "value": value,
        "limit": limit,
        "minimum": validity.minimum,
        "maximum": validity.maximum,
        "uncertainty": validity.uncertainty,
        "resolved": resolved,
        "higher_tier_enabled": higher_tier_enabled,
        "in_range": in_range,
    }
    if not resolved:
        verdict: Literal["PASS", "FAIL", "OPEN", "NEEDS-HIGHER-TIER"] = "OPEN"
        margin = None
        reason = "The analysis result is unresolved; no verdict is credited."
    elif not in_range:
        verdict = "NEEDS-HIGHER-TIER" if higher_tier_enabled else "OPEN"
        margin = None
        reason = "The result is outside this tier's validity range."
    else:
        margin = (
            limit - value - validity.uncertainty
            if relation == "max"
            else value - limit - validity.uncertainty
        )
        verdict = "PASS" if margin >= 0 else "FAIL"
        reason = f"Margin {margin:g} after uncertainty allowance {validity.uncertainty:g}."
    return DecisionRecord(
        item=item,
        tier=tier,
        relation=relation,
        used_numbers=numbers,
        verdict=verdict,
        margin=margin,
        reason=reason,
    )


def replay(record: DecisionRecord | dict[str, Any]) -> DecisionRecord:
    """Recompute a decision from its logged numbers without accessing graph values."""

    record = DecisionRecord.model_validate(record)
    numbers = record.used_numbers
    replayed = decide(
        item=record.item,
        tier=record.tier,
        value=_required_number(numbers, "value"),
        limit=_required_number(numbers, "limit"),
        relation=record.relation,
        validity=TierValidity(
            minimum=_optional_number(numbers, "minimum"),
            maximum=_optional_number(numbers, "maximum"),
            uncertainty=_required_number(numbers, "uncertainty"),
        ),
        resolved=bool(numbers["resolved"]),
        higher_tier_enabled=bool(numbers["higher_tier_enabled"]),
    )
    return replayed


def _required_number(numbers: dict[str, float | bool | None], key: str) -> float:
    value = numbers.get(key)
    if not isinstance(value, (float, int)) or isinstance(value, bool):
        raise ValueError(f"decision record is missing numeric value {key!r}")
    return float(value)


def _optional_number(numbers: dict[str, float | bool | None], key: str) -> float | None:
    value = numbers.get(key)
    if value is None:
        return None
    if not isinstance(value, (float, int)) or isinstance(value, bool):
        raise ValueError(f"decision record field {key!r} must be numeric or null")
    return float(value)
