# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest

from design_graph import (
    Connector,
    PlateModel,
    ThermalTwoNodeModel,
    route_connectors,
    simply_supported_plate_frequency,
    solve_two_node_thermal,
)


def test_plate_rule_reports_safety_factored_frequency_and_validity() -> None:
    estimate = simply_supported_plate_frequency(
        PlateModel(
            width_mm=100,
            height_mm=80,
            thickness_mm=2,
            youngs_modulus_Pa=70e9,
            density_kg_m3=2700,
            poisson_ratio=0.33,
            frequency_safety_factor=1.25,
        )
    )
    assert estimate.frequency_Hz > estimate.credited_frequency_Hz > 0
    assert estimate.uncertainty_fraction == pytest.approx(0.2)
    assert estimate.resolved is True
    assert estimate.tier == "plate-rule"


def test_two_node_thermal_network_closes_energy_balance() -> None:
    result = solve_two_node_thermal(
        ThermalTwoNodeModel(
            ambient_C=20,
            power_node_1_W=2,
            power_node_2_W=1.2,
            conductance_1_to_ambient_W_K=0.5,
            conductance_2_to_ambient_W_K=0.25,
            conductance_between_nodes_W_K=0.2,
        )
    )
    assert result.temperature_node_1_C > 20
    assert result.temperature_node_2_C > 20
    assert result.temperature_node_1_C != result.temperature_node_2_C
    assert abs(result.energy_residual_node_1_W) < 1e-10
    assert abs(result.energy_residual_node_2_W) < 1e-10


def test_connector_search_prioritizes_emc_and_uses_destination_costs() -> None:
    connectors = [
        Connector(
            part="SYN-J1",
            group="sensor",
            contacts=8,
            destination="sensor_bus",
            emc_class="normal",
            footprint_mm=14,
            wire_length_by_wall_mm={"north": 30, "east": 10},
        ),
        Connector(
            part="SYN-J2",
            group="power",
            contacts=4,
            destination="power_feed",
            emc_class="critical",
            footprint_mm=12,
            wire_length_by_wall_mm={"north": 5, "east": 25},
            route_penalty_by_wall={"east": 5},
        ),
    ]
    placements = route_connectors(
        connectors,
        walls=["north", "east"],
        destination_to_wall={"sensor_bus": "east", "power_feed": "north"},
    )
    assert [placement.group for placement in placements] == ["power", "sensor"]
    assert placements[0].wall == "north"
    assert placements[1].wall == "east"
