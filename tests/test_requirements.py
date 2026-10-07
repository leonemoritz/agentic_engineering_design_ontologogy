# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest

from design_graph import (
    DesignInputs,
    Graph,
    Ref,
    RequirementCard,
    bind_requirements,
    check_fidelity,
    rule,
)


def card(identifier: str, kind: str, value: float | str, quote: str) -> RequirementCard:
    return RequirementCard(
        id=identifier,
        kind=kind,
        params={"name": value} if kind == "load_case" else {"value": value},
        quote=quote,
        method="T",
    )


def test_fidelity_checks_numbers_units_and_kind() -> None:
    valid = card("SYN-1", "frequency_max_Hz", 1000.0, "The upper frequency is 1 kHz.")
    invalid = card("SYN-2", "frequency_max_Hz", 2000.0, "The upper frequency is 1 kHz.")
    assert check_fidelity(valid) == []
    assert "numeric parameter 2000" in check_fidelity(invalid)[0]


def test_binding_selects_strictest_and_applies_every_load_case() -> None:
    inputs, conflicts = bind_requirements(
        [
            card("SYN-1", "frequency_max_Hz", 20_000.0, "Frequency shall be at most 20 kHz."),
            card("SYN-2", "frequency_max_Hz", 12_000.0, "Frequency shall be at most 12 kHz."),
            card("SYN-3", "qsl_min_g", 8.0, "The minimum acceleration is 8 g."),
            card("SYN-4", "qsl_min_g", 10.0, "The minimum acceleration is 10 g."),
            card("SYN-5", "load_case", "launch", "Apply the launch load case."),
            card("SYN-6", "load_case", "handling", "Apply the handling load case."),
        ]
    )
    assert conflicts == []
    assert inputs.f_max_Hz is not None and inputs.f_max_Hz.value == 12_000.0
    assert inputs.f_max_Hz.sources == ["SYN-2"]
    assert inputs.qsl_g is not None and inputs.qsl_g.value == 10.0
    assert [entry.value for entry in inputs.load_cases] == ["launch", "handling"]


def test_incompatible_minimum_and_maximum_emit_decide_conflict() -> None:
    inputs, conflicts = bind_requirements(
        [
            card("SYN-1", "envelope_max_mm", 18.0, "Envelope width shall not exceed 18 mm."),
            card("SYN-2", "envelope_min_mm", 20.0, "Envelope width shall be at least 20 mm."),
        ]
    )
    assert inputs.envelope_max_mm is None
    assert len(conflicts) == 1
    assert conflicts[0].verdict == "DECIDE"
    assert conflicts[0].requirement_ids == ["SYN-1", "SYN-2"]


def test_tbc_requirement_is_provisional_and_bad_quote_cannot_bind() -> None:
    tbc = RequirementCard(
        id="SYN-1",
        kind="frequency_max_Hz",
        params={"value": 1000.0},
        quote="Frequency shall not exceed 1 kHz.",
        method="R",
        tbc=True,
    )
    inputs, _ = bind_requirements([tbc])
    assert inputs.f_max_Hz is not None
    assert inputs.f_max_Hz.provisional == ["[P] requirement is marked TBC"]

    malformed = card("SYN-2", "frequency_max_Hz", 1000.0, "Frequency shall be controlled.")
    with pytest.raises(ValueError, match="no number with a parseable unit"):
        bind_requirements([malformed])


def test_lineage_follows_only_the_sourced_driver_field() -> None:
    inputs, _ = bind_requirements(
        [
            card("SYN-FREQ", "frequency_max_Hz", 1000.0, "Frequency shall not exceed 1 kHz."),
            card("SYN-ENV", "envelope_max_mm", 20.0, "Envelope width shall not exceed 20 mm."),
        ]
    )

    @rule(version="1", source="synthetic design inputs")
    def make_design_inputs() -> DesignInputs:
        return inputs

    @rule(version="1", source="synthetic frequency consumer")
    def frequency_limit(value: float) -> float:
        return value

    @rule(version="1", source="synthetic envelope consumer")
    def envelope_limit(value: float) -> float:
        return value

    graph = (
        Graph()
        .add("design_inputs", make_design_inputs, {})
        .add("frequency", frequency_limit, {"value": Ref("design_inputs", "f_max_Hz.value")})
        .add(
            "envelope",
            envelope_limit,
            {"value": Ref("design_inputs", "envelope_max_mm.value")},
        )
    )
    graph.evaluate()
    assert graph.requirement_lineage("SYN-FREQ") == ["design_inputs", "frequency"]
    assert graph.requirement_lineage("SYN-ENV") == ["design_inputs", "envelope"]