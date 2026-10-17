# SPDX-License-Identifier: Apache-2.0
"""Small synthetic PCB enclosure vertical slice built on the graph core."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from design_graph.core import Graph, Ref, rule
from design_graph.engineering.connectors import Connector, ConnectorPlacement, route_connectors
from design_graph.engineering.geometry import (
    CircleHole,
    DFMResult,
    DraftGeometry,
    DraftRegion,
    Point2D,
    check_2d_dfm,
)
from design_graph.engineering.verification import Evidence, VerificationRow
from design_graph.model.cards import (
    DesignInputs,
    RequirementCard,
    RequirementConflict,
    bind_requirements,
)


class BoardInput(BaseModel):
    width_mm: float
    height_mm: float
    thickness_mm: float
    component_heights_mm: list[float] = Field(default_factory=list)
    mounting_holes_mm: list[Point2D] = Field(default_factory=list)
    mounting_hole_diameter_mm: float = 3.0


class LayoutParameters(BaseModel):
    clearance_mm: float
    wall_thickness_mm: float
    base_thickness_mm: float
    lid_gap_mm: float
    seam_pitch_mm: float
    vent_width_mm: float
    tool_radius_mm: float
    minimum_web_mm: float


class EnclosureProject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    board: BoardInput
    layout: LayoutParameters
    requirements: list[RequirementCard] = Field(default_factory=list)
    requirement_file: str | None = None
    layout_variant_file: str | None = None
    connectors: list[Connector] = Field(default_factory=list)
    walls: list[str] = Field(default_factory=lambda: ["north", "east", "south", "west"])
    destination_to_wall: dict[str, str] = Field(default_factory=dict)
    material_density_kg_m3: float | None = None


class BoundRequirements(BaseModel):
    design_inputs: DesignInputs
    conflicts: list[RequirementConflict]


class EnclosureLayout(BaseModel):
    board_origin_x_mm: float
    board_origin_y_mm: float
    inner_width_mm: float
    inner_height_mm: float
    outer_width_mm: float
    outer_height_mm: float
    internal_height_mm: float


class SeamDesign(BaseModel):
    pitch_mm: float


class VentDesign(BaseModel):
    width_mm: float
    cutoff_Hz: float
    margin_Hz: float


@rule(version="1", source="synthetic input carrier")
def pass_project(project: EnclosureProject) -> EnclosureProject:
    return project


@rule(version="1", source="requirement cards and strict-threshold binding")
def aggregate_requirements(cards: list[RequirementCard]) -> BoundRequirements:
    values, conflicts = bind_requirements(cards)
    return BoundRequirements(design_inputs=values, conflicts=conflicts)


@rule(version="1", source="rectangular PCB clearance placement in housing frame")
def place_board(board: BoardInput, parameters: LayoutParameters) -> EnclosureLayout:
    inner_width = board.width_mm + 2 * parameters.clearance_mm
    inner_height = board.height_mm + 2 * parameters.clearance_mm
    return EnclosureLayout(
        board_origin_x_mm=parameters.clearance_mm,
        board_origin_y_mm=parameters.clearance_mm,
        inner_width_mm=inner_width,
        inner_height_mm=inner_height,
        outer_width_mm=inner_width + 2 * parameters.wall_thickness_mm,
        outer_height_mm=inner_height + 2 * parameters.wall_thickness_mm,
        internal_height_mm=(
            max(board.component_heights_mm, default=0.0)
            + board.thickness_mm
            + parameters.lid_gap_mm
        ),
    )


@rule(version="1", source="parametric rectangular enclosure-base draft recipe")
def draft_base(
    board: BoardInput, parameters: LayoutParameters, layout: EnclosureLayout
) -> DraftGeometry:
    holes = [
        CircleHole(
            center=Point2D(
                x_mm=layout.board_origin_x_mm + point.x_mm + parameters.wall_thickness_mm,
                y_mm=layout.board_origin_y_mm + point.y_mm + parameters.wall_thickness_mm,
            ),
            radius_mm=board.mounting_hole_diameter_mm / 2,
        )
        for point in board.mounting_holes_mm
    ]
    region = DraftRegion(
        name="enclosure_base",
        boundary=[
            Point2D(x_mm=0, y_mm=0),
            Point2D(x_mm=layout.outer_width_mm, y_mm=0),
            Point2D(x_mm=layout.outer_width_mm, y_mm=layout.outer_height_mm),
            Point2D(x_mm=0, y_mm=layout.outer_height_mm),
        ],
        holes=holes,
        corner_radii_mm=[parameters.tool_radius_mm] * 4,
    )
    return DraftGeometry(regions=[region])


@rule(version="1", source="minimum internal tool radius and minimum material web")
def evaluate_dfm(geometry: DraftGeometry, parameters: LayoutParameters) -> DFMResult:
    return check_2d_dfm(
        geometry.regions[0],
        min_tool_radius_mm=parameters.tool_radius_mm,
        min_web_mm=parameters.minimum_web_mm,
    )


@rule(version="1", source="declared seam pitch parameter")
def design_seam(parameters: LayoutParameters) -> SeamDesign:
    return SeamDesign(pitch_mm=parameters.seam_pitch_mm)


@rule(version="1", source="rectangular-waveguide first-mode cutoff c/(2a)")
def design_vents(parameters: LayoutParameters, f_max_Hz: float) -> VentDesign:
    cutoff = 149_896_229.0 / parameters.vent_width_mm
    return VentDesign(
        width_mm=parameters.vent_width_mm,
        cutoff_Hz=cutoff,
        margin_Hz=cutoff - f_max_Hz,
    )


@rule(version="1", source="connector footprint, wire-length, route, and destination cost search")
def assign_connector_walls(
    connectors: list[Connector], walls: list[str], destination_to_wall: dict[str, str]
) -> list[ConnectorPlacement]:
    return route_connectors(connectors, walls, destination_to_wall)


@rule(version="1", source="verification matrix from graph results")
def verification_matrix(
    project: EnclosureProject,
    requirements: BoundRequirements,
    layout: EnclosureLayout,
    seam: SeamDesign,
    vents: VentDesign,
    dfm: DFMResult,
    connectors: list[ConnectorPlacement],
) -> list[VerificationRow]:
    inputs = requirements.design_inputs
    rows: list[VerificationRow] = []
    if inputs.envelope_max_mm is None:
        for axis, value in (("width", layout.outer_width_mm), ("height", layout.outer_height_mm)):
            rows.append(
                VerificationRow(
                    req_ids=[],
                    topic=f"envelope_{axis}",
                    requirement="Declared maximum enclosure envelope",
                    method="I",
                    evidence=Evidence(node="layout", path=f"outer_{axis}_mm"),
                    value=value,
                    margin=None,
                    verdict="OPEN",
                    provisional=True,
                    provisional_reasons=["[P] no active envelope requirement supplied"],
                )
            )
    else:
        limit = inputs.envelope_max_mm.value
        for axis, value in (("width", layout.outer_width_mm), ("height", layout.outer_height_mm)):
            margin = limit - value
            rows.append(
                VerificationRow(
                    req_ids=inputs.envelope_max_mm.sources,
                    topic=f"envelope_{axis}",
                    requirement=f"Maximum envelope dimension {limit:g} mm",
                    method="I",
                    evidence=Evidence(node="layout", path=f"outer_{axis}_mm"),
                    value=value,
                    margin=margin,
                    verdict="PASS" if margin >= 0 else "FAIL",
                    provisional=bool(inputs.envelope_max_mm.provisional),
                    provisional_reasons=inputs.envelope_max_mm.provisional,
                )
            )
    if inputs.seam_pitch_max_mm is None:
        rows.append(
            VerificationRow(
                req_ids=[],
                topic="lid_seam_pitch",
                requirement="Maximum lid seam pitch",
                method="R",
                evidence=Evidence(node="seam", path="pitch_mm"),
                value=seam.pitch_mm,
                margin=None,
                verdict="OPEN",
                provisional=True,
                provisional_reasons=["[P] no active seam pitch requirement supplied"],
            )
        )
    else:
        margin = inputs.seam_pitch_max_mm.value - seam.pitch_mm
        rows.append(
            VerificationRow(
                req_ids=inputs.seam_pitch_max_mm.sources,
                topic="lid_seam_pitch",
                requirement=f"Maximum seam pitch {inputs.seam_pitch_max_mm.value:g} mm",
                method="R",
                evidence=Evidence(node="seam", path="pitch_mm"),
                value=seam.pitch_mm,
                margin=margin,
                verdict="PASS" if margin >= 0 else "FAIL",
                provisional=bool(inputs.seam_pitch_max_mm.provisional),
                provisional_reasons=inputs.seam_pitch_max_mm.provisional,
            )
        )
    frequency_sources = inputs.f_max_Hz.sources if inputs.f_max_Hz else []
    maximum_frequency = inputs.f_max_Hz.value if inputs.f_max_Hz else 0.0
    rows.append(
        VerificationRow(
            req_ids=frequency_sources,
            topic="vent_cutoff",
            requirement=f"Vent first-mode cutoff above {maximum_frequency:g} Hz",
            method="A",
            evidence=Evidence(node="vents", path="cutoff_Hz"),
            value=vents.cutoff_Hz,
            margin=vents.margin_Hz,
            verdict="PASS" if vents.margin_Hz >= 0 else "FAIL",
            provisional=bool(inputs.f_max_Hz and inputs.f_max_Hz.provisional),
            provisional_reasons=inputs.f_max_Hz.provisional if inputs.f_max_Hz else [],
        )
    )
    for finding in dfm.findings:
        rows.append(
            VerificationRow(
                req_ids=[],
                topic=f"dfm_{finding.check}",
                requirement=finding.reason,
                method="I",
                evidence=Evidence(node="dfm", path=finding.subject),
                value=finding.margin_mm,
                margin=finding.margin_mm,
                verdict="PASS" if finding.passed else "FAIL",
            )
        )
    for placement in connectors:
        rows.append(
            VerificationRow(
                req_ids=[],
                topic=f"connector_wall_{placement.group}",
                requirement=f"Connector group assigned to {placement.wall} wall",
                method="A",
                evidence=Evidence(node="connector_walls", path=placement.group),
                value=placement.model_dump(mode="json"),
                margin=None,
                verdict="DELIVERABLE",
            )
        )
    for conflict in requirements.conflicts:
        rows.append(
            VerificationRow(
                req_ids=conflict.requirement_ids,
                topic=f"requirement_conflict_{conflict.kind}",
                requirement=conflict.reason,
                method="I",
                evidence=Evidence(node="design_inputs", path="conflicts"),
                value=conflict.values,
                margin=None,
                verdict="DECIDE",
            )
        )
    return rows


def load_project(path: str | Path) -> EnclosureProject:
    """Load and validate a synthetic enclosure project from YAML."""

    source = Path(path).resolve()
    with source.open(encoding="utf-8") as stream:
        data = yaml.safe_load(stream)
    if not isinstance(data, dict):
        raise ValueError("project YAML must contain a mapping")
    requirement_file = data.get("requirement_file")
    if requirement_file:
        if data.get("requirements"):
            raise ValueError("specify either inline requirements or requirement_file, not both")
        from design_graph.interchange.requirement_io import load_requirement_cards

        data["requirements"] = load_requirement_cards(source.parent / requirement_file)
    variant_file = data.get("layout_variant_file")
    if variant_file:
        variant_path = source.parent / variant_file
        with variant_path.open(encoding="utf-8") as variant_stream:
            variant = yaml.safe_load(variant_stream)
        if not isinstance(variant, dict):
            raise ValueError("layout variant YAML must contain a mapping")
        base_layout = data.get("layout", {})
        if not isinstance(base_layout, dict):
            raise ValueError("project layout must contain a mapping")
        data["layout"] = {**base_layout, **variant}
    return EnclosureProject.model_validate(data)


def build_enclosure_graph(project: EnclosureProject) -> Graph:
    """Build the fixed-order graph for the synthetic enclosure vertical slice."""

    graph = Graph()
    graph.add("project", pass_project, {"project": project}, stage="inputs")
    graph.add(
        "requirements", extract_requirements, {"project": Ref("project")}, stage="requirements"
    )
    graph.add(
        "design_inputs",
        aggregate_requirements,
        {"cards": Ref("requirements")},
        stage="design_inputs",
    )
    graph.add(
        "layout",
        place_board,
        {"board": Ref("project", "board"), "parameters": Ref("project", "layout")},
        stage="layout & rules",
    )
    graph.add(
        "draft",
        draft_base,
        {
            "board": Ref("project", "board"),
            "parameters": Ref("project", "layout"),
            "layout": Ref("layout"),
        },
        stage="draft (2-D)",
    )
    graph.add(
        "dfm",
        evaluate_dfm,
        {"geometry": Ref("draft"), "parameters": Ref("project", "layout")},
        stage="draft (2-D)",
    )
    graph.add("seam", design_seam, {"parameters": Ref("project", "layout")}, stage="layout & rules")
    graph.add(
        "vents",
        design_vents,
        {
            "parameters": Ref("project", "layout"),
            "f_max_Hz": Ref("design_inputs", "design_inputs.f_max_Hz.value"),
        },
        stage="layout & rules",
    )
    graph.add(
        "connector_walls",
        assign_connector_walls,
        {
            "connectors": Ref("project", "connectors"),
            "walls": Ref("project", "walls"),
            "destination_to_wall": Ref("project", "destination_to_wall"),
        },
        stage="layout & rules",
    )
    graph.add(
        "verification",
        verification_matrix,
        {
            "project": Ref("project"),
            "requirements": Ref("design_inputs"),
            "layout": Ref("layout"),
            "seam": Ref("seam"),
            "vents": Ref("vents"),
            "dfm": Ref("dfm"),
            "connectors": Ref("connector_walls"),
        },
        stage="verification",
    )
    return graph


@rule(version="1", source="project requirement list")
def extract_requirements(project: EnclosureProject) -> list[RequirementCard]:
    return project.requirements
