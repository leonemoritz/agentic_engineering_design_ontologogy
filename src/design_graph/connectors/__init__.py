# SPDX-License-Identifier: Apache-2.0
"""Connectors to open-source CAx tools, run as separate executables, with an evidence bridge."""

from design_graph.connectors.base import (
    Connector,
    ConnectorError,
    ConnectorRun,
    Reading,
    ToolInfo,
    declare_tool,
    evidence_from_run,
    parameters_from_part,
)
from design_graph.connectors.tools import CONNECTORS, get_connector

__all__ = [
    "CONNECTORS",
    "Connector",
    "ConnectorError",
    "ConnectorRun",
    "Reading",
    "ToolInfo",
    "declare_tool",
    "evidence_from_run",
    "get_connector",
    "parameters_from_part",
]
