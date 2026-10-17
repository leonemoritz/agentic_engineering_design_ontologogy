# SPDX-License-Identifier: Apache-2.0
"""Typed, provenance-aware design-intent graphs."""

from design_graph.core import (
    Graph,
    GraphError,
    GraphValidationError,
    ProvenanceRecord,
    Provisional,
    Ref,
    Sourced,
    rule,
)
from design_graph.engineering.analysis import (
    ModalEstimate,
    PlateModel,
    ThermalResult,
    ThermalTwoNodeModel,
    simply_supported_plate_frequency,
    solve_two_node_thermal,
)
from design_graph.engineering.connectors import (
    Connector,
    ConnectorPlacement,
    route_connectors,
)
from design_graph.engineering.controller import ControllerAction, Lever, SizingController
from design_graph.engineering.geometry import (
    CircleHole,
    DFMFinding,
    DFMResult,
    DraftGeometry,
    DraftRegion,
    MassProperties,
    Point2D,
    calculate_mass_properties,
    check_2d_dfm,
    export_step,
    solidify,
)
from design_graph.engineering.solver import SolverError, SolverResult, SolverTimeout, run_solver
from design_graph.engineering.verification import (
    DecisionRecord,
    Evidence,
    TierValidity,
    Verdict,
    VerificationRow,
    decide,
    replay,
)
from design_graph.interchange.report import export_pdf, render_report, write_review_package
from design_graph.interchange.requirement_io import load_reqif, load_requirement_cards
from design_graph.model.cards import (
    DesignInputs,
    RequirementCard,
    RequirementConflict,
    bind_requirements,
    check_fidelity,
)

__all__ = [
    "Graph",
    "GraphError",
    "GraphValidationError",
    "ControllerAction",
    "Connector",
    "ConnectorPlacement",
    "CircleHole",
    "DFMFinding",
    "DFMResult",
    "DecisionRecord",
    "DraftGeometry",
    "DraftRegion",
    "DesignInputs",
    "Evidence",
    "Lever",
    "MassProperties",
    "ModalEstimate",
    "PlateModel",
    "Provisional",
    "Point2D",
    "ProvenanceRecord",
    "Ref",
    "RequirementCard",
    "RequirementConflict",
    "Sourced",
    "SizingController",
    "SolverError",
    "SolverResult",
    "SolverTimeout",
    "TierValidity",
    "ThermalResult",
    "ThermalTwoNodeModel",
    "VerificationRow",
    "Verdict",
    "rule",
    "bind_requirements",
    "check_fidelity",
    "check_2d_dfm",
    "calculate_mass_properties",
    "decide",
    "replay",
    "run_solver",
    "solidify",
    "export_step",
    "export_pdf",
    "render_report",
    "write_review_package",
    "load_requirement_cards",
    "load_reqif",
    "route_connectors",
    "simply_supported_plate_frequency",
    "solve_two_node_thermal",
]
