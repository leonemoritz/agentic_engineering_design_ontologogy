# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from pathlib import Path

import pytest

from design_graph.engineering.geometry import DraftRegion, Point2D, export_step, solidify


def _sample_region() -> DraftRegion:
    return DraftRegion(
        name="synthetic_plate",
        boundary=[
            Point2D(x_mm=0, y_mm=0),
            Point2D(x_mm=20, y_mm=0),
            Point2D(x_mm=20, y_mm=10),
            Point2D(x_mm=0, y_mm=10),
        ],
    )


def test_solidify_requires_positive_height() -> None:
    with pytest.raises(ValueError, match="height must be positive"):
        solidify(_sample_region(), 0)


def test_cadquery_solid_and_step_export(tmp_path: Path) -> None:
    pytest.importorskip("cadquery")
    region = _sample_region()
    solid = solidify(region, 2)
    assert solid.val().isValid()
    step_path = export_step(region, 2, tmp_path / "synthetic.step")
    assert step_path.is_file()
    assert step_path.stat().st_size > 0
