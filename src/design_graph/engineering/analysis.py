# SPDX-License-Identifier: Apache-2.0
"""Transparent analytical structural and thermal baseline calculations."""

from __future__ import annotations

from math import pi, sqrt

from pydantic import BaseModel, model_validator

from design_graph.core import rule


class PlateModel(BaseModel):
    width_mm: float
    height_mm: float
    thickness_mm: float
    youngs_modulus_Pa: float
    density_kg_m3: float
    poisson_ratio: float
    frequency_safety_factor: float = 1.2

    @model_validator(mode="after")
    def check_properties(self) -> PlateModel:
        if min(self.width_mm, self.height_mm, self.thickness_mm) <= 0:
            raise ValueError("plate dimensions must be positive")
        if self.youngs_modulus_Pa <= 0 or self.density_kg_m3 <= 0:
            raise ValueError("plate modulus and density must be positive")
        if not 0 < self.poisson_ratio < 0.5:
            raise ValueError("Poisson ratio must be between zero and 0.5")
        if self.frequency_safety_factor < 1:
            raise ValueError("frequency safety factor must be at least 1")
        return self


class ModalEstimate(BaseModel):
    frequency_Hz: float
    credited_frequency_Hz: float
    uncertainty_fraction: float
    validity_max_thickness_ratio: float
    resolved: bool = True
    tier: str = "plate-rule"


@rule(version="1", source="simply-supported isotropic thin-plate fundamental frequency")
def simply_supported_plate_frequency(model: PlateModel) -> ModalEstimate:
    """Estimate the first plate mode; intended only for thin, isotropic, simply-supported plates."""

    width_m = model.width_mm / 1000.0
    height_m = model.height_mm / 1000.0
    thickness_m = model.thickness_mm / 1000.0
    bending_rigidity = (
        model.youngs_modulus_Pa * thickness_m**3 / (12 * (1 - model.poisson_ratio**2))
    )
    areal_mass = model.density_kg_m3 * thickness_m
    frequency = pi / 2 * sqrt(bending_rigidity / areal_mass) * (1 / width_m**2 + 1 / height_m**2)
    credited = frequency / model.frequency_safety_factor
    return ModalEstimate(
        frequency_Hz=frequency,
        credited_frequency_Hz=credited,
        uncertainty_fraction=1 - 1 / model.frequency_safety_factor,
        validity_max_thickness_ratio=0.05,
    )


class ThermalTwoNodeModel(BaseModel):
    ambient_C: float
    power_node_1_W: float
    power_node_2_W: float
    conductance_1_to_ambient_W_K: float
    conductance_2_to_ambient_W_K: float
    conductance_between_nodes_W_K: float

    @model_validator(mode="after")
    def check_conductances(self) -> ThermalTwoNodeModel:
        if (
            min(
                self.conductance_1_to_ambient_W_K,
                self.conductance_2_to_ambient_W_K,
                self.conductance_between_nodes_W_K,
            )
            <= 0
        ):
            raise ValueError("thermal conductances must be positive")
        if min(self.power_node_1_W, self.power_node_2_W) < 0:
            raise ValueError("thermal power inputs cannot be negative")
        return self


class ThermalResult(BaseModel):
    temperature_node_1_C: float
    temperature_node_2_C: float
    energy_residual_node_1_W: float
    energy_residual_node_2_W: float
    resolved: bool = True
    tier: str = "lumped-thermal"


@rule(version="1", source="steady-state two-node thermal conductance network")
def solve_two_node_thermal(model: ThermalTwoNodeModel) -> ThermalResult:
    """Solve a two-node linear thermal network and report node energy residuals."""

    first_ambient = model.conductance_1_to_ambient_W_K
    second_ambient = model.conductance_2_to_ambient_W_K
    coupling = model.conductance_between_nodes_W_K
    first_diagonal = first_ambient + coupling
    second_diagonal = second_ambient + coupling
    determinant = first_diagonal * second_diagonal - coupling**2
    rise_1 = (
        model.power_node_1_W * second_diagonal + coupling * model.power_node_2_W
    ) / determinant
    rise_2 = (model.power_node_2_W * first_diagonal + coupling * model.power_node_1_W) / determinant
    temperature_1 = model.ambient_C + rise_1
    temperature_2 = model.ambient_C + rise_2
    residual_1 = (
        model.conductance_1_to_ambient_W_K * rise_1
        + coupling * (rise_1 - rise_2)
        - model.power_node_1_W
    )
    residual_2 = (
        model.conductance_2_to_ambient_W_K * rise_2
        + coupling * (rise_2 - rise_1)
        - model.power_node_2_W
    )
    return ThermalResult(
        temperature_node_1_C=temperature_1,
        temperature_node_2_C=temperature_2,
        energy_residual_node_1_W=residual_1,
        energy_residual_node_2_W=residual_2,
    )
