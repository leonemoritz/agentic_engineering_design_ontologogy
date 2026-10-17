# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from design_graph import load_reqif, load_requirement_cards
from design_graph.cli import main
from design_graph.model.cards import check_fidelity

ROOT = Path(__file__).resolve().parents[1]


def test_load_json_requirement_cards_and_fidelity() -> None:
    cards = load_requirement_cards(ROOT / "tests/fixtures/synthetic_requirements.json")
    assert len(cards) == 1
    assert cards[0].id == "SYN-JSON-1"
    assert check_fidelity(cards[0]) == []


def test_load_synthetic_reqif_objects_and_xhtml_quote() -> None:
    cards = load_reqif(ROOT / "tests/fixtures/synthetic_requirements.reqif")
    assert len(cards) == 1
    assert cards[0].id == "SYN-REQ-XML-1"
    assert cards[0].quote == "Maximum envelope dimension is 25 mm."
    assert check_fidelity(cards[0]) == []


def test_requirement_cli_reports_validated_card_count() -> None:
    result = CliRunner().invoke(
        main,
        ["requirements", str(ROOT / "tests/fixtures/synthetic_requirements.reqif")],
    )
    assert result.exit_code == 0, result.output
    assert "Loaded 1 requirement cards" in result.output
    assert "Requirement fidelity: passed" in result.output


def test_reqif_rejects_document_type_and_entity_declarations(tmp_path: Path) -> None:
    document = tmp_path / "unsafe.reqif"
    document.write_text(
        '<!DOCTYPE x [<!ENTITY injected "synthetic">]><REQ-IF>&injected;</REQ-IF>',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="DTD or entity declarations"):
        load_reqif(document)
