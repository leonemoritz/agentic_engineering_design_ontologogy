# SPDX-License-Identifier: Apache-2.0
"""Typed, provenance-aware design-intent graphs."""

from design_graph.core import (
    Graph,
    GraphError,
    GraphValidationError,
    Provisional,
    ProvenanceRecord,
    Ref,
    Sourced,
    rule,
)
from design_graph.requirements import (
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
    "DesignInputs",
    "Provisional",
    "ProvenanceRecord",
    "Ref",
    "RequirementCard",
    "RequirementConflict",
    "Sourced",
    "rule",
    "bind_requirements",
    "check_fidelity",
]