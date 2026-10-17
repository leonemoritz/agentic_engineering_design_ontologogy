# SPDX-License-Identifier: Apache-2.0
"""Convert legacy requirement cards into draft system-model elements."""

from __future__ import annotations

from design_graph.model.cards import RequirementCard
from design_graph.model.elements import Need, Part, Relationship, Requirement, VerificationCase
from design_graph.model.system import SystemModel
from design_graph.model.units import Quantity

# Only limits whose direction is unambiguous become quantitative; the rest stay qualitative.
_QUANTITATIVE = {
    "envelope_max_mm": ("le", "mm"),
    "envelope_min_mm": ("ge", "mm"),
    "seam_pitch_max_mm": ("le", "mm"),
    "qsl_min_g": ("ge", "g"),
}


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
        relation, unit = _QUANTITATIVE.get(card.kind, (None, ""))
        numeric = next((v for v in card.params.values() if isinstance(v, (int, float))), None)
        quantitative = relation is not None and numeric is not None
        model.requirements.append(
            Requirement(
                id=card.id,
                text=card.quote,
                doc=f"Imported {card.kind} card, method {card.method}.",
                subject=subject,
                attribute=card.kind if quantitative else None,
                relation=relation if quantitative else None,  # type: ignore[arg-type]
                target=Quantity(float(numeric), unit)
                if quantitative and numeric is not None
                else None,
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
