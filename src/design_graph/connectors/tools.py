# SPDX-License-Identifier: Apache-2.0
"""Connectors for ten open-source CAx tools, each run as a separate executable."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from design_graph.connectors.base import Connector, ConnectorError, Reading, ToolInfo
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
    von_mises,
)

RESULTS_JSON = "results.json"


class _ScriptConnector(Connector):
    """Runs a script that writes `results.json` (the results contract) into its directory."""

    outputs = 'Whatever the script writes to results.json: {"name": {"value": 1.0, "unit": "mm"}}.'

    def command(self, executable: str, deck: Path, workdir: Path) -> list[str]:
        return [executable, deck.name]

    def collect(self, workdir: Path, deck: Path) -> dict[str, Reading]:
        return read_results_json(workdir / RESULTS_JSON)


class FreeCAD(_ScriptConnector):
    id = "freecad"
    name = "FreeCAD"
    category = "cad"
    license = "LGPL-2.0-or-later"
    homepage = "https://www.freecad.org"
    executables = ("freecadcmd", "FreeCADCmd", "freecad.cmd")
    version_pattern = r"FreeCAD\s+(\d+\.\d+(?:\.\d+)?)"
    inputs = "A Python script for FreeCADCmd (headless) that writes results.json."


class OpenCascade(_ScriptConnector):
    """Open CASCADE through its Python bindings (OCP, CadQuery or pythonocc), in its own Python."""

    id = "opencascade"
    name = "Open CASCADE"
    category = "cad-kernel"
    license = "LGPL-2.1-only WITH OCCT-exception-1.0"
    homepage = "https://dev.opencascade.org"
    executables = ("python3", "python")
    inputs = (
        "A Python script run by an interpreter that has OCP, CadQuery or pythonocc-core "
        "installed (set DG_OPENCASCADE_EXE); it writes results.json."
    )
    _PROBE = (
        "import importlib.metadata as m\n"
        "for d in ('cadquery-ocp', 'OCP', 'pythonocc-core'):\n"
        "    try:\n"
        "        print(d, m.version(d)); break\n"
        "    except Exception:\n"
        "        pass\n"
    )

    def _probe(self, executable: str) -> str | None:
        try:
            done = subprocess.run(
                [executable, "-c", self._PROBE], capture_output=True, text=True, timeout=30
            )
        except (OSError, subprocess.SubprocessError):
            return None
        fields = done.stdout.split()
        return f"{fields[0]} {fields[1]}" if len(fields) >= 2 else None

    def detect(self) -> ToolInfo | None:
        # Any Python is found on PATH; only one with the bindings counts as installed.
        info = super().detect()
        return info if info is not None and info.version is not None else None


class OpenSCAD(Connector):
    id = "openscad"
    name = "OpenSCAD"
    category = "cad"
    license = "GPL-2.0-or-later"
    homepage = "https://openscad.org"
    executables = ("openscad",)
    version_pattern = r"OpenSCAD version\s+(\S+)"
    inputs = "A .scad model; placeholders set its variables."
    outputs = (
        "From the exported STL: volume, area, extent_x, extent_y, extent_z "
        "(declare units={'length': ...})."
    )

    def command(self, executable: str, deck: Path, workdir: Path) -> list[str]:
        return [executable, "-o", "model.stl", deck.name]

    def collect(self, workdir: Path, deck: Path) -> dict[str, Reading]:
        metrics = mesh_metrics(read_stl(workdir / "model.stl"))
        power = {"volume": 3, "area": 2}
        return {
            name: Reading(value, self.unit_of("length", power.get(name, 1)))
            for name, value in metrics.items()
        }


class Gmsh(Connector):
    id = "gmsh"
    name = "Gmsh"
    category = "meshing"
    license = "GPL-2.0-or-later"
    homepage = "https://gmsh.info"
    executables = ("gmsh",)
    inputs = "A .geo script; option dimension (default 3)."
    outputs = "nodes, elements (counts) of the written mesh.msh."

    def command(self, executable: str, deck: Path, workdir: Path) -> list[str]:
        dimension = self.options.get("dimension", "3")
        return [executable, deck.name, f"-{dimension}", "-format", "msh41", "-o", "mesh.msh"]

    def collect(self, workdir: Path, deck: Path) -> dict[str, Reading]:
        return {k: Reading(v, "") for k, v in read_msh_counts(workdir / "mesh.msh").items()}


class CalculiX(Connector):
    id = "calculix"
    name = "CalculiX"
    category = "fea"
    license = "GPL-2.0-or-later"
    homepage = "http://www.calculix.de"
    executables = ("ccx", "ccx_2.21", "ccx_2.20")
    version_args = ("-v",)
    version_pattern = r"Version\s+(\d+(?:\.\d+)+)"
    inputs = "A .inp deck with *NODE PRINT / *EL PRINT requests so results land in the .dat file."
    outputs = (
        "Per printed set: max_displacement.<SET> (U), max_von_mises.<SET> (S), "
        "total_force.<SET> and total_force_x/y/z.<SET> (RF); other blocks as "
        "max_abs_<column>.<SET>. Declare units={'length': ..., 'stress': ..., 'force': ...}."
    )

    def command(self, executable: str, deck: Path, workdir: Path) -> list[str]:
        return [executable, "-i", deck.stem]

    def collect(self, workdir: Path, deck: Path) -> dict[str, Reading]:
        dat = workdir / f"{deck.stem}.dat"
        if not dat.is_file():
            raise ConnectorError(f"CalculiX wrote no {dat.name}")
        readings: dict[str, Reading] = {}
        for (block, node_set), rows in read_ccx_dat(dat).items():
            columns = set(rows[0])
            if block.startswith("displacements") and {"vx", "vy", "vz"} <= columns:
                value = max((r["vx"] ** 2 + r["vy"] ** 2 + r["vz"] ** 2) ** 0.5 for r in rows)
                readings[f"max_displacement.{node_set}"] = Reading(value, self.unit_of("length"))
            elif block.startswith("stresses") and {"sxx", "syy", "szz"} <= columns:
                readings[f"max_von_mises.{node_set}"] = Reading(
                    max(von_mises(r) for r in rows), self.unit_of("stress")
                )
            elif block.startswith("forces") and {"fx", "fy", "fz"} <= columns:
                unit = self.unit_of("force")
                totals = {a: sum(r[f"f{a}"] for r in rows) for a in "xyz"}
                for axis, total in totals.items():
                    readings[f"total_force_{axis}.{node_set}"] = Reading(total, unit)
                norm = sum(t**2 for t in totals.values()) ** 0.5
                readings[f"total_force.{node_set}"] = Reading(norm, unit)
            else:
                for column in sorted(columns):
                    readings[f"max_abs_{column}.{node_set}"] = Reading(
                        max(abs(r[column]) for r in rows)
                    )
        return readings


class CodeAster(_ScriptConnector):
    id = "code_aster"
    name = "code_aster"
    category = "fea"
    license = "GPL-3.0-or-later"
    homepage = "https://code-aster.org"
    executables = ("run_aster", "as_run")
    inputs = (
        "An .export file (pass the .comm and mesh as support files); the .comm command file "
        "is Python and writes results.json."
    )


class Elmer(Connector):
    id = "elmer"
    name = "Elmer FEM"
    category = "multiphysics"
    license = "GPL-2.0-or-later"
    homepage = "https://www.elmerfem.org"
    executables = ("ElmerSolver",)
    version_args = ()
    version_pattern = r"ELMER SOLVER \(v\s*([\d.]+)"
    inputs = "A .sif file with a SaveScalars solver; option scalars (default scalars.dat)."
    outputs = "The last row of the SaveScalars file, named from its .names file."

    def command(self, executable: str, deck: Path, workdir: Path) -> list[str]:
        return [executable, deck.name]

    def collect(self, workdir: Path, deck: Path) -> dict[str, Reading]:
        name = self.options.get("scalars", "scalars.dat")
        found = sorted(workdir.rglob(name))
        if not found:
            raise ConnectorError(f"Elmer wrote no {name}")
        values, _ = read_elmer_scalars(found[0])
        return {k: Reading(v) for k, v in values.items()}

    def version_from_run(self, workdir: Path, output: str) -> str | None:
        found = sorted(workdir.rglob(self.options.get("scalars", "scalars.dat")))
        if found:
            _, version = read_elmer_scalars(found[0])
            if version:
                return version
        return self.version_from_text(output)


class OpenFOAM(Connector):
    id = "openfoam"
    name = "OpenFOAM"
    category = "cfd"
    license = "GPL-3.0-or-later"
    homepage = "https://openfoam.org"
    executables = ("foamRun", "simpleFoam", "blockMesh")
    version_args = ()
    version_pattern = r"Version:\s*(v?\d[\w.\-]*)"
    deck_kind = "directory"
    inputs = (
        "A case directory. Runs ./Allrun when present, else the solver named by option solver "
        "(default foamRun). Results come from function objects."
    )
    outputs = "Last row of each postProcessing/<functionObject>/<time>/*.dat: <object>.<column>."

    def command(self, executable: str, deck: Path, workdir: Path) -> list[str]:
        if (workdir / "Allrun").is_file():
            return ["bash", "Allrun"]
        solver = self.options.get("solver")
        return [solver] if solver else [executable]

    def collect(self, workdir: Path, deck: Path) -> dict[str, Reading]:
        return {k: Reading(v) for k, v in read_foam_post_processing(workdir).items()}

    def version_from_run(self, workdir: Path, output: str) -> str | None:
        return os.environ.get("WM_PROJECT_VERSION") or self.version_from_text(output)


class SU2(Connector):
    id = "su2"
    name = "SU2"
    category = "cfd"
    license = "LGPL-2.1-or-later"
    homepage = "https://su2code.github.io"
    executables = ("SU2_CFD",)
    version_args = ()
    version_pattern = r"SU2 v(\d+\.\d+\.\d+)"
    inputs = "A .cfg file writing a CSV history; option history (default history.csv)."
    outputs = "The last row of the history file, keyed by its column headers."

    def command(self, executable: str, deck: Path, workdir: Path) -> list[str]:
        return [executable, deck.name]

    def collect(self, workdir: Path, deck: Path) -> dict[str, Reading]:
        history = workdir / self.options.get("history", "history.csv")
        return {k: Reading(v) for k, v in read_su2_history(history).items()}


class KiCad(Connector):
    id = "kicad"
    name = "KiCad"
    category = "ecad"
    license = "GPL-3.0-or-later"
    homepage = "https://www.kicad.org"
    executables = ("kicad-cli",)
    version_args = ("version",)
    inputs = "A .kicad_pcb board; runs the design-rule check."
    outputs = "violations, errors, warnings, unconnected_items, schematic_parity (counts)."

    def command(self, executable: str, deck: Path, workdir: Path) -> list[str]:
        return [
            executable,
            "pcb",
            "drc",
            "--format",
            "json",
            "--severity-all",
            "--output",
            "drc.json",
            deck.name,
        ]

    def collect(self, workdir: Path, deck: Path) -> dict[str, Reading]:
        return {k: Reading(v, "") for k, v in read_kicad_drc(workdir / "drc.json").items()}


CONNECTORS: dict[str, type[Connector]] = {
    c.id: c
    for c in (
        OpenCascade,
        FreeCAD,
        OpenSCAD,
        Gmsh,
        CalculiX,
        CodeAster,
        Elmer,
        OpenFOAM,
        SU2,
        KiCad,
    )
}


def get_connector(
    connector_id: str,
    executable: str | None = None,
    *,
    units: dict[str, str] | None = None,
    options: dict[str, str] | None = None,
) -> Connector:
    try:
        cls = CONNECTORS[connector_id]
    except KeyError:
        raise ConnectorError(
            f"unknown connector {connector_id!r}; known: {', '.join(CONNECTORS)}"
        ) from None
    return cls(executable, units=units, options=options)
