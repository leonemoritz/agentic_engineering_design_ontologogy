# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from design_graph import RequirementCard, check_fidelity
from design_graph.model import Quantity, SatisfactionStatus, assess_model, validate_model
from design_graph.model.cards_import import import_cards


def card(quote: str, **fields: Any) -> RequirementCard:
    return RequirementCard(id="R1", quote=quote, method="A", **fields)


def test_qualitative_card_needs_only_a_quote() -> None:
    assert check_fidelity(card("SYS shall have property p.")) == []


def test_fidelity_finds_dimensionless_values() -> None:
    fields = {"attribute": "x", "relation": "le"}
    assert check_fidelity(card("SYS shall have x <= 3.", value=3, **fields)) == []
    assert check_fidelity(card("SYS shall have x <= 3.", value=4, **fields)) == [
        "R1: 4 is absent from the quote"
    ]


def test_fidelity_converts_units_written_in_the_quote() -> None:
    fields = {"attribute": "l", "relation": "le", "unit": "m"}
    assert check_fidelity(card("SYS shall have l <= 25 mm.", value=0.025, **fields)) == []
    assert check_fidelity(card("SYS shall have l <= 25 mm.", value=25, **fields)) == [
        "R1: 25 m is absent from the quote"
    ]
    assert check_fidelity(card("SYS shall have l <= 25.", value=25, **fields)) != []


def test_fidelity_checks_every_bound() -> None:
    fields = {"attribute": "x", "relation": "range", "value": 0.2}
    assert check_fidelity(card("SYS shall have 0.2 <= x <= 0.6.", upper=0.6, **fields)) == []
    assert check_fidelity(card("SYS shall have 0.2 <= x <= 0.6.", upper=0.7, **fields)) == [
        "R1: 0.7 is absent from the quote"
    ]


def test_quote_without_numbers_is_reported() -> None:
    issues = check_fidelity(card("SYS shall have small x.", attribute="x", relation="le", value=3))
    assert issues == ["R1: quote contains no number"]


@pytest.mark.parametrize(
    "fields, message",
    [
        ({"relation": "le", "value": 3}, "needs an attribute and a value"),
        ({"attribute": "x", "value": 3}, "need a relation"),
        ({"attribute": "x", "relation": "eq", "value": 3}, "eq needs a tolerance"),
        ({"attribute": "x", "relation": "range", "value": 3}, "range needs an upper value"),
        ({"attribute": "x", "relation": "le", "value": 3, "unit": "nope"}, "unknown unit"),
    ],
)
def test_malformed_cards_are_rejected(fields: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        card("SYS shall have x <= 3.", **fields)


def test_imported_cards_become_blocked_requirements() -> None:
    model = import_cards(
        [
            card("SYS shall have x <= 3.", attribute="x", relation="le", value=3),
            RequirementCard(id="R2", quote="SYS shall have property p.", method="I", tbc=True),
        ],
        subject="SYS",
    )
    assert [f for f in validate_model(model) if f.severity == "error"] == []
    first, second = model.requirements
    assert (first.attribute, first.relation, first.target) == ("x", "le", Quantity(3.0))
    assert second.relation is None and second.tbc
    assert {c.id for c in model.cases} == {"C-IMPORT-A", "C-IMPORT-I"}
    assert {r.satisfaction for r in assess_model(model).requirements} == {
        SatisfactionStatus.BLOCKED
    }
