# SPDX-License-Identifier: Apache-2.0
"""Readers for the output files of the connected tools. Standard library only."""

from __future__ import annotations

import csv
import io
import json
import math
import re
import struct
from collections.abc import Iterator
from pathlib import Path

from design_graph.connectors.base import ConnectorError, Reading

Vertex = tuple[float, float, float]

# ---------------------------------------------------------------------- results.json contract


def read_results_json(path: Path) -> dict[str, Reading]:
    """`{"name": {"value": 1.0, "unit": "mm"}}` or `{"name": 1.0}` (unit then undeclared)."""

    if not path.is_file():
        raise ConnectorError(f"the script did not write {path.name}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ConnectorError(f"{path.name} must hold an object of named results")
    readings: dict[str, Reading] = {}
    for name, item in data.items():
        if isinstance(item, dict):
            readings[name] = Reading(float(item["value"]), item.get("unit"))
        elif isinstance(item, (int, float)) and not isinstance(item, bool):
            readings[name] = Reading(float(item))
        else:
            raise ConnectorError(f"{path.name}: result {name!r} is not a number")
    return readings


# ---------------------------------------------------------------------- STL


def read_stl(path: Path) -> list[tuple[Vertex, Vertex, Vertex]]:
    """Triangles of an ASCII or binary STL file."""

    data = path.read_bytes()
    if len(data) >= 84:
        (count,) = struct.unpack_from("<I", data, 80)
        if len(data) == 84 + 50 * count:
            triangles = []
            for i in range(count):
                v = struct.unpack_from("<9f", data, 84 + 50 * i + 12)
                triangles.append(((v[0], v[1], v[2]), (v[3], v[4], v[5]), (v[6], v[7], v[8])))
            return triangles
    points = [
        (float(x), float(y), float(z))
        for x, y, z in re.findall(
            r"vertex\s+(\S+)\s+(\S+)\s+(\S+)", data.decode("utf-8", errors="replace")
        )
    ]
    if not points or len(points) % 3:
        raise ConnectorError(f"{path.name} is not a readable STL file")
    return [(points[i], points[i + 1], points[i + 2]) for i in range(0, len(points), 3)]


def mesh_metrics(triangles: list[tuple[Vertex, Vertex, Vertex]]) -> dict[str, float]:
    """Enclosed volume, surface area and bounding-box extents of a closed triangle mesh."""

    volume = area = 0.0
    for a, b, c in triangles:
        u = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
        w = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
        n = (u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2], u[0] * w[1] - u[1] * w[0])
        area += math.sqrt(n[0] ** 2 + n[1] ** 2 + n[2] ** 2) / 2
        volume += (
            a[0] * (b[1] * c[2] - b[2] * c[1])
            - a[1] * (b[0] * c[2] - b[2] * c[0])
            + a[2] * (b[0] * c[1] - b[1] * c[0])
        ) / 6
    points = [p for t in triangles for p in t]
    extents = [max(p[i] for p in points) - min(p[i] for p in points) for i in range(3)]
    return {
        "volume": abs(volume),
        "area": area,
        "extent_x": extents[0],
        "extent_y": extents[1],
        "extent_z": extents[2],
    }


# ---------------------------------------------------------------------- Gmsh


def read_msh_counts(path: Path) -> dict[str, float]:
    """Node and element counts of an ASCII Gmsh mesh, format 2.2 or 4.x."""

    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    major = 2
    counts: dict[str, float] = {}
    for i, line in enumerate(lines):
        if line.strip() == "$MeshFormat" and i + 1 < len(lines):
            fields = lines[i + 1].split()
            major = int(float(fields[0]))
            if len(fields) > 1 and fields[1] != "0":
                raise ConnectorError(f"{path.name} is a binary mesh; write ASCII")
        elif line.strip() in ("$Nodes", "$Elements") and i + 1 < len(lines):
            fields = lines[i + 1].split()
            count = int(fields[1] if major >= 4 else fields[0])
            counts["nodes" if line.strip() == "$Nodes" else "elements"] = float(count)
    if set(counts) != {"nodes", "elements"}:
        raise ConnectorError(f"{path.name} has no $Nodes or $Elements section")
    return counts


# ---------------------------------------------------------------------- CalculiX


_CCX_HEADER = re.compile(
    r"^\s*([a-z][a-z .]*?)\s*\(([^)]*)\)\s*for set\s+(\S+)\s+and time\s+(\S+)", re.IGNORECASE
)
_CCX_ID_COLUMNS = {"elem", "integ.pnt.", "node"}


def read_ccx_dat(path: Path) -> dict[tuple[str, str], list[dict[str, float]]]:
    """Blocks of a CalculiX .dat file (*NODE PRINT, *EL PRINT), last time step per block."""

    blocks: dict[tuple[str, str], tuple[float, list[dict[str, float]]]] = {}
    current: tuple[str, str] | None = None
    columns: list[str] = []
    rows: list[dict[str, float]] = []
    time = 0.0

    def close() -> None:
        if current is not None and rows:
            previous = blocks.get(current)
            if previous is None or time >= previous[0]:
                blocks[current] = (time, list(rows))

    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        header = _CCX_HEADER.match(line)
        if header:
            close()
            names = [n.strip().lower() for n in header.group(2).split(",")]
            columns = [n for n in names if n not in _CCX_ID_COLUMNS]
            current = (header.group(1).strip().lower(), header.group(3))
            time = float(header.group(4))
            rows = []
            continue
        tokens = line.split()
        if current is None or not tokens:
            continue
        try:
            values = [float(t) for t in tokens]
        except ValueError:
            close()
            current = None
            continue
        if len(values) >= len(columns):
            rows.append(dict(zip(columns, values[len(values) - len(columns) :], strict=True)))
    close()
    return {key: value[1] for key, value in blocks.items()}


def von_mises(row: dict[str, float]) -> float:
    sxx, syy, szz = row["sxx"], row["syy"], row["szz"]
    sxy, sxz, syz = row["sxy"], row["sxz"], row["syz"]
    return math.sqrt(
        0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2)
        + 3 * (sxy**2 + sxz**2 + syz**2)
    )


# ---------------------------------------------------------------------- Elmer


def read_elmer_scalars(data_file: Path) -> tuple[dict[str, float], str | None]:
    """Last row of an Elmer SaveScalars file, named from its `.names` file, and the version."""

    names_file = data_file.with_name(data_file.name + ".names")
    if not data_file.is_file() or not names_file.is_file():
        raise ConnectorError(f"Elmer scalars {data_file.name} or its .names file is missing")
    names: dict[int, str] = {}
    version = None
    for line in names_file.read_text(encoding="utf-8", errors="replace").splitlines():
        found = re.match(r"^\s*(\d+):\s*(.+?)\s*$", line)
        if found:
            label = re.sub(r"^res:\s*", "", found.group(2))
            names[int(found.group(1))] = re.sub(r"\W+", "_", label).strip("_")
        elif line.lower().startswith("elmer version:"):
            version = line.split(":", 1)[1].strip()
    rows = [r.split() for r in data_file.read_text(encoding="utf-8").splitlines() if r.strip()]
    if not rows:
        raise ConnectorError(f"Elmer scalars {data_file.name} is empty")
    last = rows[-1]
    return {names.get(i + 1, f"column_{i + 1}"): float(v) for i, v in enumerate(last)}, version


# ---------------------------------------------------------------------- OpenFOAM


_FOAM_TOKEN = re.compile(r"\([^)]*\)|\S+")


def read_foam_post_processing(case: Path) -> dict[str, float]:
    """Last row of every function-object .dat file under postProcessing/, latest start time.

    Names are `<functionObject>.<column>`, with `<file>` inserted when a function object writes
    several files. Vector columns become `<column>_x`, `_y`, `_z`.
    """

    root = case / "postProcessing"
    latest: dict[tuple[str, str], tuple[float, Path]] = {}
    for path in root.rglob("*.dat"):
        parts = path.relative_to(root).parts
        if len(parts) < 3:
            continue
        try:
            start = float(parts[-2])
        except ValueError:
            continue
        key = (parts[0], path.stem)
        if key not in latest or start > latest[key][0]:
            latest[key] = (start, path)
    per_object: dict[str, int] = {}
    for function_object, _ in latest:
        per_object[function_object] = per_object.get(function_object, 0) + 1
    values: dict[str, float] = {}
    for (function_object, stem), (_, path) in sorted(latest.items()):
        single = per_object[function_object] == 1
        prefix = function_object if single else f"{function_object}.{stem}"
        values.update(_foam_last_row(path, prefix))
    if not values:
        raise ConnectorError(f"no function-object output under {root}")
    return values


def _foam_last_row(path: Path, prefix: str) -> Iterator[tuple[str, float]]:
    header: list[str] = []
    last: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("#"):
            tokens = _FOAM_TOKEN.findall(line[1:])
            if len(tokens) > 1:
                header = tokens
        elif line.strip():
            last = _FOAM_TOKEN.findall(line)
    for column, token in zip(header, last, strict=False):
        if column.lower() == "time":
            continue
        if token.startswith("("):
            for axis, part in zip("xyz", token.strip("()").split(), strict=False):
                yield f"{prefix}.{column}_{axis}", float(part)
            continue
        try:
            yield f"{prefix}.{column}", float(token)
        except ValueError:
            continue


# ---------------------------------------------------------------------- SU2


def read_su2_history(path: Path) -> dict[str, float]:
    """Last row of an SU2 history CSV file, keyed by its (unquoted, trimmed) headers."""

    if not path.is_file():
        raise ConnectorError(f"SU2 history file {path.name} is missing")
    rows = list(csv.reader(io.StringIO(path.read_text(encoding="utf-8"))))
    rows = [r for r in rows if any(cell.strip() for cell in r)]
    if len(rows) < 2:
        raise ConnectorError(f"SU2 history file {path.name} has no data row")
    header = [cell.strip().strip('"').strip() for cell in rows[0]]
    values: dict[str, float] = {}
    for name, cell in zip(header, rows[-1], strict=False):
        try:
            values[name] = float(cell)
        except ValueError:
            continue
    return values


# ---------------------------------------------------------------------- KiCad


def read_kicad_drc(path: Path) -> dict[str, float]:
    """Violation counts of a `kicad-cli pcb drc --format json` report."""

    if not path.is_file():
        raise ConnectorError(f"KiCad DRC report {path.name} is missing")
    report = json.loads(path.read_text(encoding="utf-8"))
    violations = report.get("violations", [])
    return {
        "violations": float(len(violations)),
        "errors": float(sum(1 for v in violations if v.get("severity") == "error")),
        "warnings": float(sum(1 for v in violations if v.get("severity") == "warning")),
        "unconnected_items": float(len(report.get("unconnected_items", []))),
        "schematic_parity": float(len(report.get("schematic_parity", []))),
    }
