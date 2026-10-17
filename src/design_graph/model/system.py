# SPDX-License-Identifier: Apache-2.0
"""The system model container, content hashing, and YAML persistence."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from design_graph.model.elements import (
    Approval,
    Assumption,
    Baseline,
    Evidence,
    Need,
    Part,
    Principal,
    Relationship,
    Requirement,
    ToolDeclaration,
    VerificationCase,
    digest,
)

_COLLECTIONS = (
    "principals",
    "tools",
    "needs",
    "parts",
    "requirements",
    "cases",
    "assumptions",
    "relationships",
    "evidence",
    "approvals",
    "baselines",
)


class ModelError(Exception):
    """Raised when a model file cannot be parsed into a SystemModel."""


class SystemModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format_version: int = 1
    principals: list[Principal] = Field(default_factory=list)
    tools: list[ToolDeclaration] = Field(default_factory=list)
    needs: list[Need] = Field(default_factory=list)
    parts: list[Part] = Field(default_factory=list)
    requirements: list[Requirement] = Field(default_factory=list)
    cases: list[VerificationCase] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    approvals: list[Approval] = Field(default_factory=list)
    baselines: list[Baseline] = Field(default_factory=list)

    def elements(self) -> dict[str, BaseModel]:
        """Every identified element; later duplicates are reported by validation, not here."""

        found: dict[str, BaseModel] = {}
        for name in _COLLECTIONS:
            for item in getattr(self, name):
                found.setdefault(item.id, item)
        return found

    def get(self, element_id: str) -> BaseModel | None:
        return self.elements().get(element_id)

    def principal(self, principal_id: str) -> Principal | None:
        return next((item for item in self.principals if item.id == principal_id), None)

    def related(
        self, kind: str, *, source: str | None = None, target: str | None = None
    ) -> list[str]:
        """Return the opposite end of active relationships of one kind."""

        out: list[str] = []
        for rel in self.relationships:
            if rel.withdrawn or rel.kind != kind:
                continue
            if source is not None and rel.source == source:
                out.append(rel.target)
            elif target is not None and rel.target == target:
                out.append(rel.source)
        return list(dict.fromkeys(out))


def content_hash(model: SystemModel, element_id: str) -> str:
    """Hash an element's own content; a case also covers the requirements it verifies."""

    element = model.get(element_id)
    if element is None:
        raise KeyError(element_id)
    payload: dict[str, Any] = {"content": element.model_dump(mode="json")}
    if isinstance(element, VerificationCase):
        payload["verifies"] = sorted(model.related("verify", source=element_id))
    return digest(payload)


def basis_hash(model: SystemModel, requirement_id: str, case_id: str) -> str:
    """Hash everything evidence for (requirement, case) depends on."""

    requirement = next((r for r in model.requirements if r.id == requirement_id), None)
    case = next((c for c in model.cases if c.id == case_id), None)
    if requirement is None or case is None:
        raise KeyError(f"{requirement_id}/{case_id}")
    parts: dict[str, str] = {
        "requirement": content_hash(model, requirement_id),
        "case": content_hash(model, case_id),
        "subject": content_hash(model, requirement.subject)
        if model.get(requirement.subject) is not None
        else "",
        "tool": content_hash(model, case.tool) if case.tool and model.get(case.tool) else "",
    }
    return digest(parts)


def load_model(path: str | Path) -> SystemModel:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return SystemModel.model_validate(data)
    except (OSError, yaml.YAMLError, ValidationError) as error:
        raise ModelError(f"cannot load model {path}: {error}") from error


def dump_model(model: SystemModel, path: str | Path) -> None:
    """Write stable, diff-friendly YAML with elements sorted by id and empty lists omitted."""

    data = model.model_dump(mode="json", exclude_defaults=False)
    out: dict[str, Any] = {"format_version": data["format_version"]}
    for name in _COLLECTIONS:
        items = sorted(data[name], key=lambda item: str(item["id"]))
        if items:
            out[name] = items
    Path(path).write_text(
        yaml.safe_dump(out, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8"
    )
