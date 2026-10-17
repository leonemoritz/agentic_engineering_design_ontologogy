# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from design_graph.cli import main
from design_graph.model import SatisfactionStatus, assess_model, load_model, validate_model

EXAMPLE = Path(__file__).resolve().parents[2] / "examples/model_enclosure.yaml"


def test_example_model_is_valid_and_assessed_as_documented() -> None:
    model = load_model(EXAMPLE)
    assert [f for f in validate_model(model) if f.severity == "error"] == []
    result = {r.requirement: r.satisfaction for r in assess_model(model).requirements}
    assert result == {
        "R-MASS": SatisfactionStatus.SATISFIED,
        "R-FREQ": SatisfactionStatus.NOT_SATISFIED,
        "R-SEAL": SatisfactionStatus.OPEN,
    }


def test_cli_validate_and_assess() -> None:
    runner = CliRunner()
    assert runner.invoke(main, ["model", "validate", str(EXAMPLE)]).exit_code == 0
    assessed = runner.invoke(main, ["model", "assess", str(EXAMPLE)])
    assert assessed.exit_code == 0
    assert "NOT_SATISFIED" in assessed.output
    assert runner.invoke(main, ["model", "assess", "--strict", str(EXAMPLE)]).exit_code == 1


def test_cli_assess_refuses_invalid_model(tmp_path: Path) -> None:
    broken = tmp_path / "broken.yaml"
    broken.write_text(
        "principals: [{id: a, kind: human}]\n"
        "requirements: [{id: R, text: The part shall exist., subject: ghost}]\n",
        encoding="utf-8",
    )
    result = CliRunner().invoke(main, ["model", "assess", str(broken)])
    assert result.exit_code != 0
    assert "validation errors" in result.output


def test_matrix_lists_each_requirement_case_pair() -> None:
    result = CliRunner().invoke(main, ["model", "matrix", str(EXAMPLE)])
    assert result.exit_code == 0
    assert "C-FREQ" in result.output and "FAILED" in result.output


def test_import_cards_creates_blocked_requirements(tmp_path: Path) -> None:
    fixture = Path(__file__).resolve().parents[1] / "fixtures/synthetic_requirements.json"
    out = tmp_path / "imported.yaml"
    runner = CliRunner()
    result = runner.invoke(
        main, ["model", "import-cards", str(fixture), "--subject", "SYS", "-o", str(out)]
    )
    assert result.exit_code == 0, result.output
    model = load_model(out)
    assert [f for f in validate_model(model) if f.severity == "error"] == []
    assert {r.satisfaction for r in assess_model(model).requirements} == {
        SatisfactionStatus.BLOCKED
    }
