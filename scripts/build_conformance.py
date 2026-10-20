# SPDX-License-Identifier: Apache-2.0
"""Generate the verification-semantics conformance vectors under conformance/.

Every expected value is computed by design_graph itself, so the vectors record what the
reference implementation does. conformance/verification-semantics.md specifies the same
behaviour in prose; conformance/independent/check.py reimplements it without this package.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from design_graph.model import (
    Approval,
    Assumption,
    Attestation,
    Evidence,
    Need,
    Part,
    Principal,
    Provenance,
    Quantity,
    Relationship,
    Requirement,
    SystemModel,
    ToolDeclaration,
    VerificationCase,
    assess_model,
    basis_hash,
    content_hash,
    validate_model,
)
from design_graph.model.assess import RequirementAssessment
from design_graph.model.elements import digest
from design_graph.model.system import _COLLECTIONS
from design_graph.model.units import ureg

ROOT = Path(__file__).resolve().parents[1]
SUITE = "design-graph-verification/0.1"
DAY1, DAY2, DAY3 = "2026-10-01", "2026-10-02", "2026-10-03"
RUN_HASH = digest({"run": "example", "attempt": 1})

# Values are dimensionless except in the vectors that test unit rules. units.json records how
# each unit converts to coherent SI base units.
UNITS = ["", "m", "mm", "K", "degC", "delta_degC"]


# --- model builders --------------------------------------------------------------------------


def base() -> SystemModel:
    """One system, one quantitative requirement, one analysis case. Nothing approved."""

    return SystemModel(
        principals=[
            Principal(
                id="H1",
                kind="human",
                name="Human principal",
                roles=["approver", "verifier", "witness"],
            ),
            Principal(id="A1", kind="agent", roles=["author", "reviewer"]),
        ],
        tools=[
            ToolDeclaration(
                id="T1",
                version="1.0",
                intended_use="computes attribute values",
                declared_by="H1",
            )
        ],
        needs=[Need(id="N1", stakeholder="stakeholder")],
        parts=[
            Part(
                id="SYS",
                name="System",
                attributes={"x": Quantity(3.4)},
            )
        ],
        requirements=[
            Requirement(
                id="R1",
                text="SYS shall have x >= 3.",
                subject="SYS",
                attribute="x",
                relation="ge",
                target=Quantity(3.0),
            )
        ],
        cases=[VerificationCase(id="C1", method="A", tool="T1")],
        relationships=[
            Relationship(id="rel-1", kind="refine", source="R1", target="N1"),
            Relationship(id="rel-2", kind="satisfy", source="SYS", target="R1"),
            Relationship(id="rel-3", kind="verify", source="C1", target="R1"),
        ],
    )


def approve(model: SystemModel, element: str, who: str = "H1", date: str = DAY1) -> None:
    model.approvals.append(
        Approval(
            id=f"APR-{element}-{len(model.approvals) + 1}",
            element=element,
            content_hash=content_hash(model, element),
            principal=who,
            date=date,
        )
    )


def record(
    model: SystemModel,
    id: str,
    *,
    requirement: str = "R1",
    case: str = "C1",
    value: Quantity | bool | None = None,
    uncertainty: Quantity | None = None,
    date: str = DAY2,
    provenance: bool = True,
    attestations: list[Attestation] | None = None,
) -> None:
    """Record evidence against the current basis, as a tool run would."""

    model.evidence.append(
        Evidence(
            id=id,
            case=case,
            requirement=requirement,
            created_by="A1",
            date=date,
            basis_hash=basis_hash(model, requirement, case),
            value=value,
            uncertainty=uncertainty,
            provenance=Provenance(node="SYS.x", input_hash=RUN_HASH) if provenance else None,
            attestations=attestations or [],
        )
    )


def approved() -> SystemModel:
    model = base()
    approve(model, "R1")
    approve(model, "C1")
    return model


def add_offset_unit(model: SystemModel) -> None:
    """A second requirement on attribute t, in an offset unit (degC)."""

    model.parts[0].attributes["t"] = Quantity(55.0, "degC")
    model.requirements.append(
        Requirement(
            id="R2",
            text="SYS shall have t <= 60 degC.",
            subject="SYS",
            attribute="t",
            relation="le",
            target=Quantity(60.0, "degC"),
        )
    )
    model.cases.append(VerificationCase(id="C2", method="A", tool="T1"))
    model.relationships += [
        Relationship(id="rel-4", kind="refine", source="R2", target="N1"),
        Relationship(id="rel-5", kind="satisfy", source="SYS", target="R2"),
        Relationship(id="rel-6", kind="verify", source="C2", target="R2"),
    ]
    approve(model, "R2")
    approve(model, "C2")


def add_inspection(model: SystemModel) -> None:
    """A qualitative requirement verified by inspection: needs a human verifier attestation."""

    model.requirements.append(
        Requirement(
            id="R3",
            text="SYS shall have property p.",
            subject="SYS",
        )
    )
    model.cases.append(VerificationCase(id="C3", method="I"))
    model.relationships += [
        Relationship(id="rel-7", kind="refine", source="R3", target="N1"),
        Relationship(id="rel-8", kind="satisfy", source="SYS", target="R3"),
        Relationship(id="rel-9", kind="verify", source="C3", target="R3"),
    ]
    approve(model, "R3")
    approve(model, "C3")


def attest(principal: str, role: str, outcome: str = "pass") -> Attestation:
    return Attestation.model_validate(
        {"principal": principal, "role": role, "outcome": outcome, "date": DAY2}
    )


def num(value: float) -> Quantity:
    return Quantity(value)


# --- vectors ---------------------------------------------------------------------------------


Vector = tuple[str, str, list[str], SystemModel]


def v_pass() -> Vector:
    model = approved()
    record(model, "E-1", value=num(3.4), uncertainty=num(0.1))
    return "Measured value passes with uncertainty subtracted", "", ["EPS P12"], model


def v_fail() -> Vector:
    model = approved()
    record(model, "E-1", value=num(2.9))
    return "Measured value below target fails", "", ["EPS P12", "EPS C11"], model


def v_uncertainty() -> Vector:
    model = approved()
    record(model, "E-1", value=num(3.05), uncertainty=num(0.1))
    return (
        "Uncertainty turns a nominal pass into a fail",
        "3.05 meets 3 nominally, but 3.05 - 0.1 < 3.",
        ["EPS P12"],
        model,
    )


def v_convert() -> Vector:
    model = _relation_model(
        Requirement(
            id="R4",
            text="SYS shall have l >= 3 m.",
            subject="SYS",
            attribute="l",
            relation="ge",
            target=Quantity(3.0, "m"),
        ),
        attributes={"l": Quantity(3.4, "m")},
    )
    record(model, "E-1", requirement="R4", value=Quantity(3400.0, "mm"))
    return (
        "Value in a different unit of the same dimension",
        "3400 mm against 3 m; the margin is 0.4 m.",
        ["EPS P12"],
        model,
    )


def v_dimension() -> Vector:
    model = approved()
    record(model, "E-1", value=Quantity(3.4, "m"))
    return (
        "Value of the wrong dimension is invalid evidence",
        "A length against a dimensionless target. Invalid evidence leaves the case INCOMPLETE; "
        "it is neither a pass nor a fail.",
        ["EPS P12"],
        model,
    )


def v_offset() -> Vector:
    model = approved()
    add_offset_unit(model)
    record(model, "E-1", value=num(3.4))
    record(
        model,
        "E-2",
        requirement="R2",
        case="C2",
        value=Quantity(55.0, "degC"),
        uncertainty=Quantity(2.0, "delta_degC"),
    )
    return (
        "Offset temperature unit with a delta uncertainty",
        "degC values convert to kelvin with the offset; the margin is 60 - 55 - 2 = 3 K.",
        ["EPS P12"],
        model,
    )


def v_offset_uncertainty() -> Vector:
    model = approved()
    add_offset_unit(model)
    record(model, "E-1", value=num(3.4))
    record(
        model,
        "E-2",
        requirement="R2",
        case="C2",
        value=Quantity(55.0, "degC"),
        uncertainty=Quantity(2.0, "degC"),
    )
    return (
        "Uncertainty in an offset unit is invalid",
        "An uncertainty is a difference, so it must use delta_degC or K, not degC.",
        ["EPS P12"],
        model,
    )


def v_stale_requirement() -> Vector:
    model = approved()
    record(model, "E-1", value=num(3.4))
    model.requirements[0] = model.requirements[0].model_copy(
        update={"target": num(3.2), "text": "SYS shall have x >= 3.2."}
    )
    approve(model, "R1", date=DAY3)
    return (
        "Evidence goes stale when the requirement changes",
        "The edited requirement is re-approved, but the evidence was recorded against the old "
        "target and no longer counts.",
        ["EPS P13", "EPS C10"],
        model,
    )


def v_stale_approval() -> Vector:
    model = approved()
    record(model, "E-1", value=num(3.4))
    model.requirements[0] = model.requirements[0].model_copy(update={"target": num(3.2)})
    return (
        "An edit without re-approval leaves the approval stale",
        "The approval is pinned to the old content hash; a new revision does not inherit it.",
        ["EPS P03", "EPS P13", "EPS C10"],
        model,
    )


def v_stale_tool() -> Vector:
    model = approved()
    record(model, "E-1", value=num(3.4))
    model.tools[0] = model.tools[0].model_copy(update={"version": "1.1"})
    return (
        "Evidence goes stale when the declared tool changes",
        "The case approval stays valid (the case did not change), but the evidence basis "
        "includes the tool, so the result must be rerun.",
        ["EPS P05", "EPS P13"],
        model,
    )


def v_agent_approval() -> Vector:
    model = base()
    model.principals[1] = model.principals[1].model_copy(
        update={"roles": ["author", "reviewer", "approver"]}
    )
    approve(model, "R1", who="A1")
    approve(model, "C1")
    record(model, "E-1", value=num(3.4))
    return (
        "An agent approval is ignored",
        "Only a human principal with the approver role can approve. Validation also reports "
        "the agent's approver role; assessment ignores the approval regardless.",
        ["EPS P18", "EPS C15"],
        model,
    )


def v_agent_attestation() -> Vector:
    model = approved()
    add_inspection(model)
    record(model, "E-1", value=num(3.4))
    record(
        model,
        "E-2",
        requirement="R3",
        case="C3",
        provenance=False,
        attestations=[attest("A1", "verifier")],
    )
    return (
        "An agent attestation is ignored",
        "Inspection needs a human verifier attestation; the agent's attestation does not count.",
        ["EPS P11", "EPS P18", "EPS C15"],
        model,
    )


def v_human_attestation() -> Vector:
    model = approved()
    add_inspection(model)
    record(model, "E-1", value=num(3.4))
    record(
        model,
        "E-2",
        requirement="R3",
        case="C3",
        provenance=False,
        attestations=[attest("H1", "verifier")],
    )
    return "A human verifier attestation verifies an inspection", "", ["EPS P11"], model


def v_latest() -> Vector:
    model = approved()
    record(model, "E-1", value=num(2.9), date=DAY2)
    record(model, "E-2", value=num(3.4), date=DAY3)
    return (
        "The latest live evidence decides",
        "Evidence is ordered by date, then id; the earlier failing result is kept but does "
        "not decide.",
        ["EPS P12", "EPS P14"],
        model,
    )


def v_no_provenance() -> Vector:
    model = approved()
    record(model, "E-1", value=num(3.4), provenance=False)
    return "Analysis evidence without provenance is incomplete", "", ["EPS P10", "EPS P12"], model


def v_assumption() -> Vector:
    model = approved()
    model.assumptions.append(
        Assumption(
            id="AS1",
            statement="AS1 holds.",
            owner="H1",
            impacts=["R1"],
        )
    )
    record(model, "E-1", value=num(3.4))
    return (
        "An open assumption blocks satisfaction",
        "Verification passes, but satisfaction is BLOCKED until the assumption is closed.",
        ["EPS P09"],
        model,
    )


def _decomposed(x: float) -> SystemModel:
    model = approved()
    add_offset_unit(model)
    model.requirements.append(Requirement(id="R0", text="SYS shall meet N1.", subject="SYS"))
    model.relationships += [
        Relationship(id="rel-10", kind="refine", source="R0", target="N1"),
        Relationship(id="rel-11", kind="satisfy", source="SYS", target="R0"),
        Relationship(id="rel-12", kind="derive", source="R1", target="R0"),
        Relationship(id="rel-13", kind="derive", source="R2", target="R0"),
    ]
    approve(model, "R0")
    record(model, "E-1", value=num(x))
    record(model, "E-2", requirement="R2", case="C2", value=Quantity(55.0, "degC"))
    return model


def v_decomposition() -> Vector:
    return (
        "A parent requirement is verified through its derived requirements",
        "R0 has no case of its own; it is VERIFIED because R1 and R2 are SATISFIED.",
        ["EPS P12"],
        _decomposed(3.4),
    )


def v_decomposition_fail() -> Vector:
    return (
        "A failed derived requirement fails its parent",
        "",
        ["EPS P12", "EPS C11"],
        _decomposed(2.9),
    )


def v_test_witness() -> Vector:
    model = base()
    model.cases[0] = model.cases[0].model_copy(update={"method": "T"})
    approve(model, "R1")
    approve(model, "C1")
    record(model, "E-1", value=num(3.3), provenance=False)
    record(model, "E-2", value=num(3.3), provenance=False, attestations=[attest("H1", "witness")])
    return (
        "A test needs a measured value and a human witness",
        "E-1 has no witness and is incomplete; E-2 is witnessed and decides.",
        ["EPS P11", "EPS P12"],
        model,
    )


def _relation_model(
    requirement: Requirement, method: str = "A", attributes: dict[str, Quantity] | None = None
) -> SystemModel:
    """The base model with R1 replaced by another requirement on SYS."""

    model = base()
    model.parts[0].attributes.update(attributes or {})
    model.requirements = [requirement]
    model.cases[0] = model.cases[0].model_copy(update={"method": method})
    model.relationships = [
        Relationship(id="rel-1", kind="refine", source=requirement.id, target="N1"),
        Relationship(id="rel-2", kind="satisfy", source="SYS", target=requirement.id),
        Relationship(id="rel-3", kind="verify", source="C1", target=requirement.id),
    ]
    approve(model, requirement.id)
    approve(model, "C1")
    return model


def v_equal() -> Vector:
    model = _relation_model(
        Requirement(
            id="R5",
            text="SYS shall have x = 2.0 within 0.1.",
            subject="SYS",
            attribute="x",
            relation="eq",
            target=num(2.0),
            tolerance=num(0.1),
        )
    )
    record(model, "E-1", requirement="R5", value=num(2.06))
    return (
        "Equality within a tolerance",
        "The margin is 0.1 - |2.06 - 2.0| = 0.04.",
        ["EPS P12"],
        model,
    )


def v_range() -> Vector:
    model = _relation_model(
        Requirement(
            id="R6",
            text="SYS shall have 0.2 <= x <= 0.6.",
            subject="SYS",
            attribute="x",
            relation="range",
            target=num(0.2),
            upper=num(0.6),
        )
    )
    record(model, "E-1", requirement="R6", value=num(0.45))
    return (
        "Value inside a range",
        "The margin is the distance to the nearer bound: 0.6 - 0.45 = 0.15.",
        ["EPS P12"],
        model,
    )


def v_bool() -> Vector:
    model = _relation_model(
        Requirement(
            id="R7",
            text="SYS shall have b = true.",
            subject="SYS",
            attribute="b",
            relation="bool",
            expected=True,
        ),
        method="T",
    )
    record(
        model,
        "E-1",
        requirement="R7",
        value=True,
        provenance=False,
        attestations=[attest("H1", "witness")],
    )
    return "A witnessed boolean test result", "", ["EPS P11", "EPS P12"], model


def v_unapproved_case() -> Vector:
    model = base()
    approve(model, "R1")
    record(model, "E-1", value=num(3.4))
    return (
        "Evidence against an unapproved case is not assessed",
        "",
        ["EPS P06"],
        model,
    )


VECTORS: list[Callable[[], Vector]] = [
    v_pass,
    v_fail,
    v_uncertainty,
    v_convert,
    v_dimension,
    v_offset,
    v_offset_uncertainty,
    v_stale_requirement,
    v_stale_approval,
    v_stale_tool,
    v_agent_approval,
    v_agent_attestation,
    v_human_attestation,
    v_latest,
    v_no_provenance,
    v_assumption,
    v_decomposition,
    v_decomposition_fail,
    v_test_witness,
    v_equal,
    v_range,
    v_bool,
    v_unapproved_case,
]


# --- hashing primitives ----------------------------------------------------------------------

# JSON text in, canonical text and digest out. Integer versus float follows the lexical form.
HASHING_INPUTS: list[tuple[str, str]] = [
    ("key order", '{"b": 1, "a": {"d": 2, "c": 3}}'),
    ("empty containers and literals", '{"x": [], "y": {}, "t": true, "f": false, "n": null}'),
    ("integer stays integer", '{"tier": 2}'),
    ("float keeps its fraction", '{"value": 2.0}'),
    ("negative zero", '{"value": -0.0}'),
    ("short round-trip digits", '{"value": 0.1}'),
    ("fixed notation up to 1e16", '{"value": 1e15}'),
    ("exponent from 1e16", '{"value": 1e16}'),
    ("exponent below 1e-4", '{"value": 0.00001}'),
    ("fixed notation at 1e-4", '{"value": 0.0001}'),
    ("long mantissa", '{"value": 123456789012345678.0}'),
    ("small exponent padded", '{"value": 1.5e-7}'),
    ("list order is significant", '{"roles": ["witness", "approver"]}'),
    ("non-ASCII escaped", '{"text": "Wärme"}'),
    ("astral character as surrogate pair", '{"text": "\\ud83d\\udd25"}'),
    ("control and delete characters", '{"text": "a\\tb\\u0001c\\u007f"}'),
    ("quotes and backslashes", '{"text": "say \\"hi\\" \\\\ bye"}'),
]


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


# --- projection of design_graph results ------------------------------------------------------


def _codes(reasons: list[Any]) -> list[str]:
    return sorted({r.code for r in reasons})


def _margin(q: Quantity | None) -> float | None:
    return None if q is None else q.value


def project(item: RequirementAssessment) -> dict[str, Any]:
    return {
        "requirement": item.requirement,
        "verification": item.verification.value,
        "satisfaction": item.satisfaction.value,
        "margin_si": _margin(item.margin),
        "derived": sorted(item.derived),
        "reasons": _codes(item.reasons),
        "cases": [
            {
                "case": case.case,
                "status": case.status.value,
                "deciding_evidence": case.deciding_evidence,
                "reasons": _codes(case.reasons),
                "evidence": [
                    {
                        "evidence": ev.evidence,
                        "outcome": ev.outcome,
                        "margin_si": _margin(ev.margin),
                        "reasons": _codes(ev.reasons),
                    }
                    for ev in case.evidence
                ],
            }
            for case in item.cases
        ],
    }


def expected(model: SystemModel) -> dict[str, Any]:
    ids = [item.id for name in _COLLECTIONS for item in getattr(model, name)]
    pairs = sorted(
        {(rel.target, rel.source) for rel in model.relationships if rel.kind == "verify"}
        | {(ev.requirement, ev.case) for ev in model.evidence}
    )
    return {
        "validation_errors": sorted(
            {f.code for f in validate_model(model) if f.severity == "error"}
        ),
        "content_hashes": {i: content_hash(model, i) for i in ids},
        "basis_hashes": {f"{r}|{c}": basis_hash(model, r, c) for r, c in pairs},
        "assessment": [project(item) for item in assess_model(model).requirements],
    }


def units() -> dict[str, Any]:
    table: dict[str, Any] = {}
    for symbol in UNITS:
        one = Quantity(1.0, symbol)
        zero = Quantity(0.0, symbol)
        dims = ureg.Quantity(1.0, symbol).dimensionality
        table[symbol] = {
            "factor": one.to_base() - zero.to_base(),
            "offset": zero.to_base(),
            "dimension": {str(k).strip("[]"): int(v) for k, v in sorted(dims.items())},
        }
    return table


def _dump(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def build() -> dict[str, str]:
    """Return {relative path under conformance/: file text}."""

    files: dict[str, str] = {}
    for number, make in enumerate(VECTORS, start=1):
        title, description, references, model = make()
        slug = make.__name__.removeprefix("v_").replace("_", "-")
        vid = f"V{number:02d}"
        files[f"vectors/{vid}-{slug}.json"] = _dump(
            {
                "suite": SUITE,
                "id": vid,
                "title": title,
                "description": description,
                "references": references,
                "model": model.model_dump(mode="json"),
                "expected": expected(model),
            }
        )
    hashing = []
    for title, text in HASHING_INPUTS:
        text_out = canonical(json.loads(text))
        hashing.append(
            {
                "title": title,
                "input": text,
                "canonical": text_out,
                "sha256": digest(json.loads(text)),
            }
        )
    files["hashing.json"] = _dump({"suite": SUITE, "cases": hashing})
    files["units.json"] = _dump({"suite": SUITE, "units": units()})
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "conformance")
    parser.add_argument("--check", action="store_true", help="fail if committed files differ")
    args = parser.parse_args(argv)
    stale = []
    for relative, text in build().items():
        path = args.out / relative
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                stale.append(relative)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    if stale:
        print("stale conformance files: " + ", ".join(stale), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
