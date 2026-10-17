# SPDX-License-Identifier: Apache-2.0
"""Reproducible synthetic benchmark for the plate estimate and thermal network."""

from __future__ import annotations

import json
import platform
import time

from design_graph.engineering.analysis import (
    PlateModel,
    ThermalTwoNodeModel,
    simply_supported_plate_frequency,
    solve_two_node_thermal,
)


def main() -> None:
    plate = PlateModel(
        width_mm=100,
        height_mm=80,
        thickness_mm=2,
        youngs_modulus_Pa=70e9,
        density_kg_m3=2700,
        poisson_ratio=0.33,
        frequency_safety_factor=1.2,
    )
    thermal = ThermalTwoNodeModel(
        ambient_C=20,
        power_node_1_W=2,
        power_node_2_W=1.2,
        conductance_1_to_ambient_W_K=0.5,
        conductance_2_to_ambient_W_K=0.25,
        conductance_between_nodes_W_K=0.2,
    )
    started = time.perf_counter()
    modal_result = simply_supported_plate_frequency(plate)
    thermal_result = solve_two_node_thermal(thermal)
    elapsed = time.perf_counter() - started
    print(
        json.dumps(
            {
                "benchmark": "synthetic-analytical-baseline-v1",
                "python": platform.python_version(),
                "elapsed_s": elapsed,
                "modal": modal_result.model_dump(mode="json"),
                "thermal": thermal_result.model_dump(mode="json"),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
