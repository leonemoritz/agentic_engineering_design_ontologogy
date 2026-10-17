# SPDX-License-Identifier: Apache-2.0
"""Generate the single-file, offline graph viewer."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any


def render_viewer(graph_data: dict[str, Any]) -> str:
    """Embed validated graph JSON into the packaged standalone HTML viewer."""

    if graph_data.get("format_version") != 1 or not isinstance(graph_data.get("nodes"), dict):
        raise ValueError("expected graph.json format_version 1 with a nodes object")
    payload = json.dumps(graph_data, ensure_ascii=True, separators=(",", ":"))
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    template = (
        files("design_graph.interchange")
        .joinpath("assets/graph-viewer.html")
        .read_text(encoding="utf-8")
    )
    return template.replace("__GRAPH_DATA__", payload)
