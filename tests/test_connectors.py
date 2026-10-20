# SPDX-License-Identifier: Apache-2.0
"""Connectors: staging and placeholders, output readers, runs with fake tools, evidence."""

from __future__ import annotations

import json
import shutil
import struct
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from design_graph.cli import main
from design_graph.connectors import (
    CONNECTORS,
    ConnectorError,
    evidence_from_run,
    get_connector,
    parameters_from_part,
)
from design_graph.connectors.base import render
from design_graph.connectors.readers import (
    mesh_metrics,
    read_ccx_dat,
    read_elmer_scalars,
    read_foam_post_processing,
    read_kicad_drc,
    read_msh_counts,
    read_results_json,
    read_stl,
    read_su2_history,
)
from design_graph.connectors.tools import CalculiX, FreeCAD, OpenFOAM
from design_graph.model import assess_model, load_model
from design_graph.model.units import Quantity

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "model_system.yaml"

# A script deck: the parameter x arrives through a placeholder, the result through results.json.
SCRIPT_DECK = (
    "import json\n"
    "x = {{x}}\n"
    "json.dump({'x': {'value': x, 'unit': ''}, 'raw': x * 2}, open('results.json', 'w'))\n"
)


def test_ten_connectors_are_registered() -> None:
    assert len(CONNECTORS) == 10
    assert {"freecad", "opencascade", "openfoam", "calculix"} <= set(CONNECTORS)
    with pytest.raises(ConnectorError, match="unknown connector"):
        get_connector("nope")


# ---------------------------------------------------------------------- placeholders


def test_render_converts_units_and_rejects_ambiguity() -> None:
    used: set[str] = set()
    params = {"a": Quantity(1.5, "m"), "b": 2.0}
    assert render("{{a:mm}} {{ b }}", params, used) == "1500 2"
    assert used == {"a", "b"}
    with pytest.raises(ConnectorError, match="needs a unit"):
        render("{{a}}", params, set())
    with pytest.raises(ConnectorError, match="has no parameter"):
        render("{{c}}", params, set())
    with pytest.raises(ConnectorError, match="bare number"):
        render("{{b:mm}}", params, set())
    with pytest.raises(ConnectorError, match="cannot convert"):
        render("{{a:s}}", params, set())


def test_unused_parameter_is_an_error(tmp_path: Path) -> None:
    deck = tmp_path / "deck.py"
    deck.write_text(SCRIPT_DECK)
    connector = FreeCAD(sys.executable)
    with pytest.raises(ConnectorError, match="not used"):
        connector.run(deck, tmp_path / "w", parameters={"x": 1.0, "typo": 2.0})


def test_missing_executable_names_the_override(tmp_path: Path) -> None:
    deck = tmp_path / "deck.py"
    deck.write_text(SCRIPT_DECK)
    with pytest.raises(ConnectorError, match="DG_FREECAD_EXE"):
        FreeCAD(str(tmp_path / "absent")).run(deck, tmp_path / "w", parameters={"x": 1.0})


# ---------------------------------------------------------------------- script connectors


def test_script_run_is_reused_until_an_input_changes(tmp_path: Path) -> None:
    deck = tmp_path / "deck.py"
    deck.write_text(SCRIPT_DECK)
    connector = FreeCAD(sys.executable)
    first = connector.run(deck, tmp_path / "w", parameters={"x": 2.5})
    assert first.outputs["x"] == Quantity(2.5, "")
    assert first.undeclared == ["raw"]
    again = connector.run(deck, tmp_path / "w", parameters={"x": 2.5})
    assert again.reused and again.input_hash == first.input_hash
    changed = connector.run(deck, tmp_path / "w", parameters={"x": 2.6})
    assert not changed.reused and changed.input_hash != first.input_hash
    assert changed.outputs["x"].value == pytest.approx(2.6)


def test_units_option_declares_an_output(tmp_path: Path) -> None:
    deck = tmp_path / "deck.py"
    deck.write_text(SCRIPT_DECK)
    run = FreeCAD(sys.executable, units={"raw": "mm"}).run(
        deck, tmp_path / "w", parameters={"x": 1.0}
    )
    assert run.outputs["raw"] == Quantity(2.0, "mm") and run.undeclared == []


def test_evidence_from_run_is_assessed(tmp_path: Path) -> None:
    model = load_model(EXAMPLE)
    deck = tmp_path / "deck.py"
    deck.write_text(SCRIPT_DECK.replace("{{x}}", "{{x}} - 0.5"))
    params = parameters_from_part(model, "SYS", ["x"])
    run = FreeCAD(sys.executable).run(deck, tmp_path / "w", parameters=params)
    common = {"case": "C1", "requirement": "R1", "created_by": "A1", "date": "2026-03-02"}
    evidence = evidence_from_run(model, run, output="x", id="E9", **common)
    assert evidence.provenance is not None
    assert evidence.provenance.node == "freecad:x"
    assert evidence.provenance.input_hash == run.input_hash
    model.evidence.append(evidence)
    item = next(r for r in assess_model(model).requirements if r.requirement == "R1")
    assert item.satisfaction.value == "SATISFIED"
    assert item.margin is not None and item.margin.value == pytest.approx(0.7)
    with pytest.raises(ConnectorError, match="has no unit"):
        evidence_from_run(model, run, output="raw", id="E10", **common)
    with pytest.raises(ConnectorError, match="no output"):
        evidence_from_run(model, run, output="nope", id="E11", **common)


def test_evidence_refuses_another_tool_version(tmp_path: Path) -> None:
    class VersionedTool(FreeCAD):
        version_pattern = r"Python\s+(\S+)"

    model = load_model(EXAMPLE)
    deck = tmp_path / "deck.py"
    deck.write_text(SCRIPT_DECK)
    run = VersionedTool(sys.executable).run(deck, tmp_path / "w", parameters={"x": 1.0})
    assert run.tool_version is not None
    with pytest.raises(ConnectorError, match="declares T1 version 1"):
        evidence_from_run(
            model,
            run,
            output="x",
            id="E9",
            case="C1",
            requirement="R1",
            created_by="A1",
            date="2026-03-02",
        )


# ---------------------------------------------------------------------- process connectors


def _fake_tool(path: Path, body: str) -> str:
    path.write_text(f"#!{sys.executable}\nimport sys, pathlib\n{body}")
    path.chmod(0o755)
    return str(path)


CCX_DAT = """
 displacements (vx,vy,vz) for set NOUT and time  0.1000000E+01

         1  3.000000E+00  4.000000E+00  0.000000E+00
         2  1.000000E+00  0.000000E+00  0.000000E+00

 stresses (elem, integ.pnt.,sxx,syy,szz,sxy,sxz,syz) for set EALL and time  0.1000000E+01

         1   1  1.000000E+00  0.000000E+00  0.000000E+00  0.000000E+00  0.000000E+00  0.000000E+00
         1   2  2.000000E+00  0.000000E+00  0.000000E+00  0.000000E+00  0.000000E+00  0.000000E+00

 forces (fx,fy,fz) for set FIX and time  0.1000000E+01

         5 -1.000000E+00  0.000000E+00  0.000000E+00
         6 -2.000000E+00  0.000000E+00  0.000000E+00
"""


def test_calculix_with_a_fake_solver(tmp_path: Path) -> None:
    (tmp_path / "result.dat").write_text(CCX_DAT)
    fake = _fake_tool(
        tmp_path / "ccx",
        f"stem = sys.argv[2]\n"
        f"text = pathlib.Path(stem + '.inp').read_text()\n"
        f"assert '2.5' in text, text\n"
        f"pathlib.Path(stem + '.dat').write_text(pathlib.Path({str(tmp_path / 'result.dat')!r})"
        f".read_text())\n",
    )
    deck = tmp_path / "job.inp"
    deck.write_text("*NODE\n1, {{x:mm}}, 0, 0\n")
    connector = CalculiX(fake, units={"length": "mm", "stress": "MPa"})
    run = connector.run(deck, tmp_path / "w", parameters={"x": Quantity(2.5, "mm")})
    assert run.outputs["max_displacement.NOUT"] == Quantity(5.0, "mm")
    assert run.outputs["max_von_mises.EALL"] == Quantity(2.0, "MPa")
    assert run.outputs["total_force_x.FIX"].value == pytest.approx(-3.0)
    assert set(run.undeclared) == {
        "total_force.FIX",
        "total_force_x.FIX",
        "total_force_y.FIX",
        "total_force_z.FIX",
    }


def test_openfoam_case_directory_keeps_dollar_macros(tmp_path: Path) -> None:
    case = tmp_path / "case"
    (case / "system").mkdir(parents=True)
    (case / "system" / "controlDict").write_text("endTime {{n}};\nvalue $internalField;\n")
    (case / "Allrun").write_text(
        "mkdir -p postProcessing/probe/0\n"
        "grep -q 'endTime 4;' system/controlDict || exit 3\n"
        "printf '# Time\\tp\\tU\\n1\\t0.5\\t(1 2 3)\\n2\\t0.25\\t(4 5 6)\\n'"
        " > postProcessing/probe/0/probe.dat\n"
    )
    run = OpenFOAM(sys.executable).run(case, tmp_path / "w", parameters={"n": 4.0})
    assert run.command[:2] == ["bash", "Allrun"]
    assert run.outputs["probe.p"].value == 0.25
    assert run.outputs["probe.U_z"].value == 6.0
    assert "$internalField" in (tmp_path / "w" / "system" / "controlDict").read_text()


# ---------------------------------------------------------------------- readers


def _cube() -> list[tuple[tuple[float, float, float], ...]]:
    v = [(x, y, z) for x in (0.0, 1.0) for y in (0.0, 1.0) for z in (0.0, 1.0)]
    faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    return [t for a, b, c, d in faces for t in ((v[a], v[b], v[c]), (v[a], v[c], v[d]))]


def test_stl_ascii_and_binary(tmp_path: Path) -> None:
    triangles = _cube()
    ascii_lines = ["solid s"]
    for tri in triangles:
        ascii_lines += ["facet normal 0 0 0", "outer loop"]
        ascii_lines += [f"vertex {x} {y} {z}" for x, y, z in tri]
        ascii_lines += ["endloop", "endfacet"]
    (tmp_path / "a.stl").write_text("\n".join([*ascii_lines, "endsolid s"]))
    binary = bytearray(b"\0" * 80) + struct.pack("<I", len(triangles))
    for tri in triangles:
        binary += struct.pack("<12fH", 0, 0, 0, *[c for p in tri for c in p], 0)
    (tmp_path / "b.stl").write_bytes(bytes(binary))
    for name in ("a.stl", "b.stl"):
        metrics = mesh_metrics(read_stl(tmp_path / name))
        assert metrics["volume"] == pytest.approx(1.0)
        assert metrics["area"] == pytest.approx(6.0)
        assert metrics["extent_z"] == pytest.approx(1.0)


def test_msh_counts_v2_and_v4(tmp_path: Path) -> None:
    (tmp_path / "v2.msh").write_text(
        "$MeshFormat\n2.2 0 8\n$EndMeshFormat\n$Nodes\n3\n$EndNodes\n$Elements\n1\n$EndElements\n"
    )
    (tmp_path / "v4.msh").write_text(
        "$MeshFormat\n4.1 0 8\n$EndMeshFormat\n$Nodes\n1 5 1 5\n$EndNodes\n"
        "$Elements\n1 2 1 2\n$EndElements\n"
    )
    assert read_msh_counts(tmp_path / "v2.msh") == {"nodes": 3.0, "elements": 1.0}
    assert read_msh_counts(tmp_path / "v4.msh") == {"nodes": 5.0, "elements": 2.0}


def test_ccx_dat_keeps_the_last_time(tmp_path: Path) -> None:
    later = CCX_DAT.replace("0.1000000E+01", "0.2000000E+01").replace("3.000000E+00", "6.0E+00")
    (tmp_path / "j.dat").write_text(CCX_DAT + later)
    blocks = read_ccx_dat(tmp_path / "j.dat")
    assert blocks[("displacements", "NOUT")][0]["vx"] == 6.0
    assert len(blocks[("stresses", "EALL")]) == 2


def test_elmer_scalars(tmp_path: Path) -> None:
    (tmp_path / "scalars.dat").write_text("1 2\n3 4\n")
    (tmp_path / "scalars.dat.names").write_text(
        "Elmer version: 9.0\nVariables in columns of matrix: scalars.dat\n"
        "   1: res: a max\n   2: b mean\n"
    )
    values, version = read_elmer_scalars(tmp_path / "scalars.dat")
    assert values == {"a_max": 3.0, "b_mean": 4.0} and version == "9.0"


def test_foam_post_processing_takes_the_latest_start(tmp_path: Path) -> None:
    for start, value in (("0", "1"), ("10", "2")):
        folder = tmp_path / "postProcessing" / "f" / start
        folder.mkdir(parents=True)
        (folder / "f.dat").write_text(f"# Time q\n{start} {value}\n")
    assert read_foam_post_processing(tmp_path) == {"f.q": 2.0}


def test_su2_history(tmp_path: Path) -> None:
    (tmp_path / "history.csv").write_text('"Inner_Iter","  a  ","b"\n0,1.0,2.0\n1,1.5,2.5\n')
    assert read_su2_history(tmp_path / "history.csv") == {"Inner_Iter": 1.0, "a": 1.5, "b": 2.5}


def test_kicad_drc_counts(tmp_path: Path) -> None:
    report = {
        "violations": [{"severity": "error"}, {"severity": "warning"}],
        "unconnected_items": [{}],
    }
    (tmp_path / "drc.json").write_text(json.dumps(report))
    counts = read_kicad_drc(tmp_path / "drc.json")
    assert counts["violations"] == 2 and counts["errors"] == 1 and counts["unconnected_items"] == 1


def test_results_json_rejects_non_numbers(tmp_path: Path) -> None:
    (tmp_path / "results.json").write_text('{"a": "text"}')
    with pytest.raises(ConnectorError, match="not a number"):
        read_results_json(tmp_path / "results.json")


# ---------------------------------------------------------------------- CLI and templates


def test_cli_lists_and_records(tmp_path: Path) -> None:
    runner = CliRunner()
    listed = runner.invoke(main, ["connectors", "list", "--no-detect"])
    assert listed.exit_code == 0 and listed.output.count("    in:") == 10
    model_file = tmp_path / "model.yaml"
    shutil.copy(EXAMPLE, model_file)
    deck = tmp_path / "deck.py"
    deck.write_text(SCRIPT_DECK)
    args = ["connectors", "run", "freecad", str(deck), "-w", str(tmp_path / "w")]
    args += ["--exe", sys.executable, "-p", "x=2.5", "--record", str(model_file)]
    args += ["--output", "x", "--case", "C1", "--requirement", "R1", "--by", "A1"]
    args += ["--evidence-id", "E9", "--date", "2026-03-02"]
    result = runner.invoke(main, args)
    assert result.exit_code == 0, result.output
    assert "recorded E9" in result.output
    assert any(e.id == "E9" for e in load_model(model_file).evidence)
    again = runner.invoke(main, args)
    assert again.exit_code != 0 and "not recorded" in again.output


def test_requirements_engineering_templates(tmp_path: Path) -> None:
    runner = CliRunner()
    cards = ROOT / "templates" / "requirement-cards.yaml"
    assert runner.invoke(main, ["requirements", str(cards)]).exit_code == 0
    model_file = tmp_path / "model.yaml"
    imported = runner.invoke(
        main, ["model", "import-cards", str(cards), "--subject", "SYS", "-o", str(model_file)]
    )
    assert imported.exit_code == 0, imported.output
    template = ROOT / "templates" / "requirements-engineering.yaml"
    status = runner.invoke(
        main, ["process", "status", str(model_file), "--template", str(template)]
    )
    assert status.exit_code == 0, status.output
    assert status.output.splitlines()[0].startswith("requirements   in_progress")
