# SPDX-License-Identifier: Apache-2.0
"""Recipe-first 2-D geometry, DFM checks, and optional CadQuery export."""

from __future__ import annotations

from collections.abc import Iterator
from math import hypot
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator


class Point2D(BaseModel):
    x_mm: float
    y_mm: float


class CircleHole(BaseModel):
    center: Point2D
    radius_mm: float

    @model_validator(mode="after")
    def positive_radius(self) -> CircleHole:
        if self.radius_mm <= 0:
            raise ValueError("hole radius must be positive")
        return self


class DraftRegion(BaseModel):
    name: str
    boundary: list[Point2D]
    holes: list[CircleHole] = Field(default_factory=list)
    corner_radii_mm: list[float] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_polygon(self) -> DraftRegion:
        if len(self.boundary) < 3:
            raise ValueError("region boundary requires at least three points")
        if self.corner_radii_mm and len(self.corner_radii_mm) != len(self.boundary):
            raise ValueError("corner radius count must match boundary vertex count")
        return self


class DraftGeometry(BaseModel):
    regions: list[DraftRegion]


class DFMFinding(BaseModel):
    check: str
    subject: str
    margin_mm: float
    passed: bool
    reason: str


class DFMResult(BaseModel):
    findings: list[DFMFinding]

    @property
    def passed(self) -> bool:
        return all(finding.passed for finding in self.findings)


class MassProperties(BaseModel):
    volume_mm3: float
    density_kg_m3: float
    mass_kg: float
    center_of_mass_mm: tuple[float, float, float]


def check_2d_dfm(
    region: DraftRegion,
    *,
    min_tool_radius_mm: float,
    min_web_mm: float,
) -> DFMResult:
    """Check internal-corner tool radii and hole-to-boundary/inter-hole webs."""

    if min_tool_radius_mm < 0 or min_web_mm < 0:
        raise ValueError("DFM limits cannot be negative")
    findings: list[DFMFinding] = []
    radii = region.corner_radii_mm or [0.0] * len(region.boundary)
    for index, radius in enumerate(radii):
        margin = radius - min_tool_radius_mm
        findings.append(
            DFMFinding(
                check="tool_radius",
                subject=f"{region.name}.corner_{index}",
                margin_mm=margin,
                passed=margin >= 0,
                reason=f"Corner radius {radius:g} mm vs tool radius {min_tool_radius_mm:g} mm.",
            )
        )
    for index, hole in enumerate(region.holes):
        boundary_gap = (
            min(
                _distance_to_segment(hole.center, start, end)
                for start, end in _segments(region.boundary)
            )
            - hole.radius_mm
        )
        margin = boundary_gap - min_web_mm
        findings.append(
            DFMFinding(
                check="hole_edge_web",
                subject=f"{region.name}.hole_{index}",
                margin_mm=margin,
                passed=margin >= 0,
                reason=f"Hole edge web {boundary_gap:g} mm vs minimum {min_web_mm:g} mm.",
            )
        )
    for first_index, first in enumerate(region.holes):
        for second_index in range(first_index + 1, len(region.holes)):
            second = region.holes[second_index]
            gap = (
                hypot(
                    first.center.x_mm - second.center.x_mm,
                    first.center.y_mm - second.center.y_mm,
                )
                - first.radius_mm
                - second.radius_mm
            )
            margin = gap - min_web_mm
            findings.append(
                DFMFinding(
                    check="hole_hole_web",
                    subject=f"{region.name}.holes_{first_index}_{second_index}",
                    margin_mm=margin,
                    passed=margin >= 0,
                    reason=f"Hole-to-hole web {gap:g} mm vs minimum {min_web_mm:g} mm.",
                )
            )
    return DFMResult(findings=findings)


def solidify(region: DraftRegion, height_mm: float) -> Any:
    """Build a single extruded region using the optional CadQuery dependency."""

    if height_mm <= 0:
        raise ValueError("solid height must be positive")
    try:
        import cadquery as cq  # type: ignore[import-not-found]
    except ImportError as error:
        raise RuntimeError("solidification requires the optional 'cad' extra (CadQuery)") from error
    points = [(point.x_mm, point.y_mm) for point in region.boundary]
    solid = cq.Workplane("XY").polyline(points).close().extrude(height_mm)
    radii = region.corner_radii_mm
    if radii and any(radius > 0 for radius in radii):
        if len(set(radii)) != 1:
            raise ValueError("CadQuery solidification currently requires a uniform corner radius")
        solid = solid.edges("|Z").fillet(radii[0])
    for hole in region.holes:
        cutter = (
            cq.Workplane("XY")
            .center(hole.center.x_mm, hole.center.y_mm)
            .circle(hole.radius_mm)
            .extrude(height_mm)
        )
        solid = solid.cut(cutter)
    return solid


def calculate_mass_properties(
    region: DraftRegion, height_mm: float, density_kg_m3: float
) -> MassProperties:
    """Compute volume, center of mass, and mass from the solidified draft recipe."""

    if density_kg_m3 <= 0:
        raise ValueError("material density must be positive")
    solid = solidify(region, height_mm).val()
    volume_mm3 = float(solid.Volume())
    center = solid.Center()
    return MassProperties(
        volume_mm3=volume_mm3,
        density_kg_m3=density_kg_m3,
        mass_kg=volume_mm3 * 1e-9 * density_kg_m3,
        center_of_mass_mm=(float(center.x), float(center.y), float(center.z)),
    )


def export_step(region: DraftRegion, height_mm: float, path: str | Path) -> Path:
    """Solidify and export a STEP file as an explicit output-side effect."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    solidify(region, height_mm).val().exportStep(str(target))
    return target


def _segments(points: list[Point2D]) -> Iterator[tuple[Point2D, Point2D]]:
    for index, point in enumerate(points):
        yield point, points[(index + 1) % len(points)]


def _distance_to_segment(point: Point2D, start: Point2D, end: Point2D) -> float:
    dx = end.x_mm - start.x_mm
    dy = end.y_mm - start.y_mm
    length_squared = dx * dx + dy * dy
    if length_squared == 0:
        return hypot(point.x_mm - start.x_mm, point.y_mm - start.y_mm)
    projection = max(
        0.0,
        min(
            1.0,
            ((point.x_mm - start.x_mm) * dx + (point.y_mm - start.y_mm) * dy) / length_squared,
        ),
    )
    nearest_x = start.x_mm + projection * dx
    nearest_y = start.y_mm + projection * dy
    return hypot(point.x_mm - nearest_x, point.y_mm - nearest_y)
