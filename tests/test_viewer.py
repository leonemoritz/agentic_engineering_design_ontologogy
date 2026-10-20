# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
from importlib.resources import files

import pytest
from click.testing import CliRunner

from design_graph.cli import main
from design_graph.interchange.viewer import render_viewer


def test_render_viewer_embeds_data_and_escapes_script_delimiters() -> None:
    graph = {
        "format_version": 1,
        "nodes": {
            "source </script>": {
                "function": "tests.source",
                "version": "1",
                "source": "synthetic",
                "stage": "inputs",
                "input_types": {},
                "bindings": {},
                "provenance": None,
                "requirement_sources": ["SYN-1"],
                "value_preview": None,
            }
        },
        "assumptions": {},
    }
    html = render_viewer(graph)

    assert "__GRAPH_DATA__" not in html
    assert "\\u003c/script\\u003e" in html
    assert '"SYN-1"' in html
    assert "function closure" in html
    assert "pointerdown" in html


def test_render_viewer_rejects_unknown_schema() -> None:
    with pytest.raises(ValueError, match="format_version 1"):
        render_viewer({"nodes": {}})


def test_viewer_script_uses_es2017_compatible_syntax() -> None:
    asset = files("design_graph.interchange").joinpath("assets/graph-viewer.html")
    source = asset.read_text(encoding="utf-8")
    for unsupported in ("?.", "??", "Object.fromEntries", ".flatMap(", ".replaceChildren("):
        assert unsupported not in source


def test_view_cli_generates_single_html_file(tmp_path) -> None:
    graph_path = tmp_path / "graph.json"
    output_path = tmp_path / "graph.html"
    graph_path.write_text(json.dumps({"format_version": 1, "nodes": {}, "assumptions": {}}))

    result = CliRunner().invoke(main, ["view", str(graph_path), "--output", str(output_path)])

    assert result.exit_code == 0, result.output
    assert output_path.exists()
    assert "Agentic Engineering Design Ontology" in output_path.read_text(encoding="utf-8")
