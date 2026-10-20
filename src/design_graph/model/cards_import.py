# SPDX-License-Identifier: Apache-2.0
"""Convert requirement cards into draft system-model elements."""

from __future__ import annotations

from design_graph.model.cards import RequirementCard
from design_graph.model.elements import Need, Part, Relationship, Requirement, VerificationCase
from design_graph.model.system import SystemModel
from design_graph.model.units import Quantity


def import_cards(
    cards: list[RequirementCard], *, subject: str, need: str = "N-IMPORTED"
) -> SystemModel:
    """Create unapproved requirements, one case per verification method, and trace links.

    Nothing is approved or attested, so every imported requirement is BLOCKED until a human acts.
    """

    model = SystemModel(
        needs=[Need(id=need, stakeholder="imported", rationale="Imported requirement cards")],
        parts=[Part(id=subject)],
    )
    methods: dict[str, str] = {}
    for card in cards:
        model.requirements.append(
            Requirement(
                id=card.id,
                text=card.quote,
                doc=f"Imported card, method {card.method}.",
                subject=subject,
                attribute=card.attribute,
                relation=card.relation,
                target=_quantity(card.value, card.unit),
                upper=_quantity(card.upper, card.unit),
                tolerance=_quantity(card.tolerance, card.unit),
                tbc=card.tbc,
                withdrawn=card.status == "withdrawn",
            )
        )
        case_id = methods.setdefault(card.method, f"C-IMPORT-{card.method}")
        model.relationships += [
            Relationship(id=f"refine-{card.id}", kind="refine", source=card.id, target=need),
            Relationship(id=f"verify-{card.id}", kind="verify", source=case_id, target=card.id),
        ]
    for method, case_id in methods.items():
        model.cases.append(
            VerificationCase(id=case_id, method=method)  # type: ignore[arg-type]
        )
    return model


def _quantity(value: float | None, unit: str) -> Quantity | None:
    return None if value is None else Quantity(value, unit)
