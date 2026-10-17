# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from pathlib import Path

import pytest

from design_graph.interchange.viewer import render_viewer


def test_viewer_lineage_selection_and_responsive_states(tmp_path: Path) -> None:
    playwright_api = pytest.importorskip("playwright.sync_api")
    graph = {
        "format_version": 1,
        "assumptions": {"[P] synthetic fallback": ["design_input"]},
        "nodes": {
            "design_input": {
                "function": "demo.input",
                "version": "1",
                "source": "synthetic card binding",
                "stage": "design_inputs",
                "input_types": {},
                "bindings": {},
                "provenance": {
                    "provisional_reasons": ["[P] synthetic fallback"],
                    "cache_hit": False,
                    "wall_time_s": 0.001,
                },
                "requirement_sources": ["SYN-REQ-1"],
                "value_preview": {"limit": 12},
            },
            "verification": {
                "function": "demo.verify",
                "version": "1",
                "source": "synthetic check",
                "stage": "verification",
                "input_types": {"limit": "float"},
                "bindings": {"limit": {"ref": "design_input", "path": "limit"}},
                "provenance": {
                    "provisional_reasons": ["[P] synthetic fallback"],
                    "cache_hit": False,
                    "wall_time_s": 0.002,
                },
                "requirement_sources": ["SYN-REQ-1"],
                "value_preview": {"margin": 2},
            },
            "unrelated": {
                "function": "demo.other",
                "version": "1",
                "source": "unrelated synthetic check",
                "stage": "verification",
                "input_types": {},
                "bindings": {},
                "provenance": None,
                "requirement_sources": [],
                "value_preview": None,
            },
        },
    }
    html_path = tmp_path / "graph.html"
    html_path.write_text(render_viewer(graph), encoding="utf-8")
    screenshots = tmp_path / "screenshots"
    screenshots.mkdir()
    errors: list[str] = []

    with playwright_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except playwright_api.Error as error:
            if "Executable doesn't exist" in str(error):
                pytest.skip(f"Playwright Chromium is not installed: {error}")
            raise
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(html_path.resolve().as_uri(), wait_until="load")
        page.screenshot(path=str(screenshots / "overview.png"), full_page=True)
        assert page.locator(".stage").all_text_contents() == ["design_inputs", "verification"]

        page.locator("#lineage").select_option("SYN-REQ-1")
        visible = page.locator(".node:not(.dimmed)").evaluate_all(
            "elements => elements.map(element => element.dataset.node)"
        )
        assert visible == ["design_input", "verification"]
        page.screenshot(path=str(screenshots / "lineage.png"), full_page=True)

        page.locator('[data-node="verification"]').click()
        assert page.locator("#details").inner_text().find("[P] synthetic fallback") >= 0
        page.screenshot(path=str(screenshots / "selection.png"), full_page=True)

        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=str(screenshots / "mobile.png"), full_page=True)
        assert page.locator("#details").is_visible()
        assert not errors
        browser.close()

    assert {path.name for path in screenshots.iterdir()} == {
        "overview.png",
        "lineage.png",
        "selection.png",
        "mobile.png",
    }
