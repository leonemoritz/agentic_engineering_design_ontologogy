# SPDX-License-Identifier: Apache-2.0
"""Verification matrix: one row per (requirement, verification case) with derived status."""

from __future__ import annotations

from pydantic import BaseModel

from design_graph.model.assess import Assessment, assess_model
from design_graph.model.system import SystemModel


class MatrixRow(BaseModel):
    requirement: str
    satisfaction: str
    case: str
    method: str
    level: str
    stage: str
    status: str
    evidence: str | None


def verification_matrix(
    model: SystemModel, assessment: Assessment | None = None
) -> list[MatrixRow]:
    result = assessment or assess_model(model)
    cases = {case.id: case for case in model.cases}
    rows: list[MatrixRow] = []
    for item in result.requirements:
        if not item.cases:
            rows.append(
                MatrixRow(
                    requirement=item.requirement,
                    satisfaction=item.satisfaction.value,
                    case="-",
                    method="-",
                    level="",
                    stage="",
                    status=item.verification.value,
                    evidence=None,
                )
            )
        for case_result in item.cases:
            case = cases[case_result.case]
            rows.append(
                MatrixRow(
                    requirement=item.requirement,
                    satisfaction=item.satisfaction.value,
                    case=case.id,
                    method=case.method,
                    level=case.level,
                    stage=case.stage,
                    status=case_result.status.value,
                    evidence=case_result.deciding_evidence,
                )
            )
    return rows
