# SPDX-License-Identifier: Apache-2.0
"""MCP server for the system model and process layer (design section 3.8, phase 7).

`tools` holds the tool logic as plain functions with no MCP dependency; `server` registers them
with the MCP SDK (optional extra `mcp`) and runs over stdio as `design-graph-mcp`.
"""

from design_graph.mcp.tools import ToolFailure, Workspace

__all__ = ["ToolFailure", "Workspace"]
