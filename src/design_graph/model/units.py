# SPDX-License-Identifier: Apache-2.0
"""Dimension-checked quantities backed by a single shared pint registry."""

from __future__ import annotations

from typing import Any

from pint import DimensionalityError, OffsetUnitCalculusError, UndefinedUnitError, UnitRegistry
from pydantic import BaseModel, ConfigDict, field_validator

ureg: Any = UnitRegistry()


class Quantity(BaseModel):
    """A magnitude with a unit string; empty unit means dimensionless."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: float
    unit: str = ""

    @field_validator("unit")
    @classmethod
    def _parseable_unit(cls, unit: str) -> str:
        try:
            ureg.Quantity(1.0, unit)
        except (UndefinedUnitError, AttributeError, ValueError, TypeError) as error:
            raise ValueError(f"unknown unit {unit!r}") from error
        return unit

    def __init__(self, value: float, unit: str = "", **data: Any) -> None:
        super().__init__(value=value, unit=unit, **data)

    @property
    def is_offset(self) -> bool:
        """True for units such as degC where differences and absolutes differ."""

        try:
            ureg.Quantity(1.0, self.unit) * 2
        except OffsetUnitCalculusError:
            return True
        return False

    @property
    def dimension(self) -> str:
        return str(ureg.Quantity(1.0, self.unit).dimensionality)

    @property
    def base_unit(self) -> str:
        return f"{ureg.Quantity(1.0, self.unit).to_base_units().units:~}"

    def to_base(self) -> float:
        """Magnitude in coherent base units (kelvin for degC, so offsets are honoured)."""

        return float(ureg.Quantity(self.value, self.unit).to_base_units().magnitude)

    def to(self, unit: str) -> float:
        try:
            return float(ureg.Quantity(self.value, self.unit).to(unit).magnitude)
        except DimensionalityError as error:
            raise ValueError(f"cannot convert {self.unit!r} to {unit!r}") from error


def same_dimension(first: Quantity, second: Quantity) -> bool:
    return bool(ureg.Quantity(1.0, first.unit).is_compatible_with(ureg.Quantity(1.0, second.unit)))
