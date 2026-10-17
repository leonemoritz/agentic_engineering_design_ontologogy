# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import zipfile
from pathlib import Path

from click.testing import CliRunner

from design_graph.cli import main
from design_graph.domains.enclosure import build_enclosure_graph, load_project
from design_graph.engineering.geometry import CircleHole, DraftRegion, Point2D, check_2d_dfm

ROOT = Path(__file__).resolve().parents[1]


def test_dfm_accepts_and_rejects_synthetic_web_and_tool_radius() -> None:
    good = DraftRegion(
        name="good",
        boundary=[
            Point2D(x_mm=0, y_mm=0),
            Point2D(x_mm=50, y_mm=0),
            Point2D(x_mm=50, y_mm=40),
            Point2D(x_mm=0, y_mm=40),
        ],
        holes=[CircleHole(center=Point2D(x_mm=5, y_mm=5), radius_mm=1)],
        corner_radii_mm=[2, 2, 2, 2],
    )
    bad = good.model_copy(
        update={
            "holes": [CircleHole(center=Point2D(x_mm=1, y_mm=1), radius_mm=2)],
            "corner_radii_mm": [0, 2, 2, 2],
        }
    )
    assert check_2d_dfm(good, min_tool_radius_mm=1, min_web_mm=2).passed
    result = check_2d_dfm(bad, min_tool_radius_mm=1, min_web_mm=2)
    assert not result.passed
    assert {finding.check for finding in result.findings if not finding.passed} == {
        "tool_radius",
        "hole_edge_web",
    }


def test_synthetic_enclosure_graph_has_sourced_rows_and_provisional_open_rows() -> None:
    project = load_project(ROOT / "examples" / "board_a.yaml")
    graph = build_enclosure_graph(project)
    rows = graph.evaluate("verification")["verification"]
    assert rows
    assert any("SYN-REQ-1" in row.req_ids for row in rows)
    assert any("SYN-REQ-3" in row.req_ids and row.provisional for row in rows)
    assert any(row.topic == "connector_wall_power" and row.verdict == "DELIVERABLE" for row in rows)
    assert all(row.verdict in {"PASS", "FAIL", "OPEN", "DECIDE", "DELIVERABLE"} for row in rows)
    assert graph.provenance("vents").source == "rectangular-waveguide first-mode cutoff c/(2a)"
    assert "[P] requirement is marked TBC" in graph.provenance("design_inputs").provisional_reasons
    assert "[P] requirement is marked TBC" in graph.provenance("verification").provisional_reasons


def test_project_can_load_external_reqif_relative_to_project(tmp_path: Path) -> None:
    reqif = ROOT / "tests/fixtures/synthetic_requirements.reqif"
    (tmp_path / "requirements.reqif").write_bytes(reqif.read_bytes())
    variant = ROOT / "examples/layout_variant_a.yaml"
    (tmp_path / "layout_variant_a.yaml").write_bytes(variant.read_bytes())
    project_data = (ROOT / "examples/board_a.yaml").read_text(encoding="utf-8")
    project_data = project_data[: project_data.index("requirements:\n")]
    project_data += "requirement_file: requirements.reqif\n"
    project_path = tmp_path / "project.yaml"
    project_path.write_text(project_data, encoding="utf-8")

    project = load_project(project_path)

    assert [card.id for card in project.requirements] == ["SYN-REQ-XML-1"]


def test_external_layout_variant_overrides_base_parameters(tmp_path: Path) -> None:
    project_data = (ROOT / "examples/board_a.yaml").read_text(encoding="utf-8")
    project_data = project_data.replace(
        "layout_variant_file: layout_variant_a.yaml",
        "layout_variant_file: layout_variant.yaml",
    )
    project_path = tmp_path / "variant-project.yaml"
    project_path.write_text(project_data, encoding="utf-8")
    (tmp_path / "layout_variant.yaml").write_text(
        "wall_thickness_mm: 3\nclearance_mm: 4\n", encoding="utf-8"
    )

    project = load_project(project_path)

    assert project.layout.wall_thickness_mm == 3
    assert project.layout.clearance_mm == 4
    assert project.layout.seam_pitch_mm == 18


def test_dg_run_writes_matrix_graph_and_standalone_viewer(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        main,
        [
            "run",
            str(ROOT / "examples" / "board_a.yaml"),
            "--no-solvers",
            "--output-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    matrix = json.loads((tmp_path / "verification.json").read_text(encoding="utf-8"))
    graph = json.loads((tmp_path / "graph.json").read_text(encoding="utf-8"))
    viewer = (tmp_path / "graph.html").read_text(encoding="utf-8")
    report = (tmp_path / "report.html").read_text(encoding="utf-8")
    assert matrix and "SYN-REQ-1" in json.dumps(matrix)
    assert "verification" in graph["nodes"]
    assert "__GRAPH_DATA__" not in viewer
    assert "Assumptions register" in report
    with zipfile.ZipFile(tmp_path / "review_package.zip") as package:
        assert set(package.namelist()) >= {
            "graph.json",
            "verification.json",
            "graph.html",
            "report.html",
            "review_template.yaml",
        }
