# SPDX-License-Identifier: Apache-2.0
"""Independent checker for the design-graph verification vectors.

Implements conformance/verification-semantics.md using only the Python standard library. It
does not import design_graph, pydantic or pint, and reads only the vector files and
units.json. Run with `python -I conformance/independent/check.py conformance`.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

COLLECTIONS = [
    "principals", "tools", "needs", "parts", "requirements", "cases", "assumptions",
    "relationships", "evidence", "approvals", "baselines", "statements", "design_rules",
    "concepts", "trade_studies", "load_cases", "failure_modes", "optimizations", "allowables",
    "credibility", "reviews", "consistency_checks",
]  # fmt: skip
TOLERANCE = 1e-9

# --- section 2: canonical JSON and digest ----------------------------------------------------


def _string(text: str) -> str:
    out = ['"']
    for unit in text.encode("utf-16-be").hex(" ", 2).split():  # UTF-16 code units
        code = int(unit, 16)
        char = chr(code)
        if char in '"\\':
            out.append("\\" + char)
        elif char in "\b\f\n\r\t":
            out.append({"\b": "\\b", "\f": "\\f", "\n": "\\n", "\r": "\\r", "\t": "\\t"}[char])
        elif 0x20 <= code <= 0x7E:
            out.append(char)
        else:
            out.append(f"\\u{code:04x}")
    out.append('"')
    return "".join(out)


def _float(x: float) -> str:
    if not math.isfinite(x):
        raise ValueError("NaN and infinity are not allowed")
    if x == 0:
        return "-0.0" if math.copysign(1, x) < 0 else "0.0"
    # Shortest round-trip digits; every mainstream language provides them.
    mantissa, _, exp_text = f"{x:.17e}".partition("e")
    for precision in range(17):
        candidate = f"{x:.{precision}e}"
        if float(candidate) == x:
            mantissa, _, exp_text = candidate.partition("e")
            break
    sign = "-" if mantissa.startswith("-") else ""
    digits = mantissa.lstrip("-").replace(".", "")
    exponent = int(exp_text)
    if -4 <= exponent < 16:
        if exponent >= 0:
            whole = digits[: exponent + 1].ljust(exponent + 1, "0")
            frac = digits[exponent + 1 :] or "0"
        else:
            whole, frac = "0", "0" * (-exponent - 1) + digits
        return f"{sign}{whole}.{frac}"
    head = digits[0] + ("." + digits[1:] if len(digits) > 1 else "")
    return f"{sign}{head}e{'-' if exponent < 0 else '+'}{abs(exponent):02d}"


def canonical(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _float(value)
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, list):
        return "[" + ",".join(canonical(v) for v in value) + "]"
    if isinstance(value, dict):
        items = sorted(value.items(), key=lambda kv: kv[0])
        return "{" + ",".join(_string(k) + ":" + canonical(v) for k, v in items) + "}"
    raise TypeError(f"not JSON: {type(value).__name__}")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("ascii")).hexdigest()


# --- model access ----------------------------------------------------------------------------


class Model:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.by_id: dict[str, dict[str, Any]] = {}
        for name in COLLECTIONS:
            for item in data.get(name, []):
                self.by_id.setdefault(item["id"], item)

    def items(self, name: str) -> list[dict[str, Any]]:
        return list(self.data.get(name, []))

    def related(
        self, kind: str, *, source: str | None = None, target: str | None = None
    ) -> list[str]:
        out: list[str] = []
        for rel in self.items("relationships"):
            if rel["withdrawn"] or rel["kind"] != kind:
                continue
            if source is not None and rel["source"] == source:
                out.append(rel["target"])
            elif target is not None and rel["target"] == target:
                out.append(rel["source"])
        return list(dict.fromkeys(out))

    def principal(self, pid: str) -> dict[str, Any] | None:
        return next((p for p in self.items("principals") if p["id"] == pid), None)


# --- section 3: content and basis hashes -----------------------------------------------------


def content_hash(model: Model, element_id: str) -> str:
    element = model.by_id[element_id]
    payload: dict[str, Any] = {"content": element}
    if any(element is case for case in model.items("cases")):
        payload["verifies"] = sorted(model.related("verify", source=element_id))
    return digest(payload)


def basis_hash(model: Model, req_id: str, case_id: str) -> str:
    req = next(r for r in model.items("requirements") if r["id"] == req_id)
    case = next(c for c in model.items("cases") if c["id"] == case_id)
    subject, tool = req["subject"], case["tool"]
    return digest(
        {
            "requirement": content_hash(model, req_id),
            "case": content_hash(model, case_id),
            "subject": content_hash(model, subject) if subject in model.by_id else "",
            "tool": content_hash(model, tool) if tool and tool in model.by_id else "",
        }
    )


def approval_state(model: Model, element_id: str) -> str:
    current = content_hash(model, element_id)
    state = "none"
    for approval in model.items("approvals"):
        if approval["withdrawn"] or approval["element"] != element_id:
            continue
        who = model.principal(approval["principal"])
        if who is None or who["kind"] != "human" or "approver" not in who["roles"]:
            continue
        if approval["content_hash"] == current:
            return "approved"
        state = "stale"
    return state


# --- section 4: quantities -------------------------------------------------------------------


class Units:
    def __init__(self, table: dict[str, Any]) -> None:
        self.table = table

    def base(self, q: dict[str, Any]) -> float:
        unit = self.table[q["unit"]]
        return float(q["value"]) * float(unit["factor"]) + float(unit["offset"])

    def same_dimension(self, a: dict[str, Any], b: dict[str, Any]) -> bool:
        return bool(self.table[a["unit"]]["dimension"] == self.table[b["unit"]]["dimension"])

    def is_offset(self, q: dict[str, Any]) -> bool:
        return bool(self.table[q["unit"]]["offset"] != 0)


def _quantity(value: Any) -> bool:
    return isinstance(value, dict) and "value" in value


def compute(units: Units, req: dict[str, Any], ev: dict[str, Any]) -> tuple[bool, float | None]:
    value = ev["value"]
    if req["relation"] == "bool":
        if not isinstance(value, bool):
            raise ValueError("bool requirement needs a bool value")
        return value == req["expected"], None
    target = req["target"]
    if not _quantity(value) or target is None:
        raise ValueError("numeric requirement needs a quantity value and a target")
    if not units.same_dimension(value, target):
        raise ValueError("dimension mismatch")
    uncertainty = 0.0
    if ev["uncertainty"] is not None:
        if not units.same_dimension(ev["uncertainty"], target):
            raise ValueError("uncertainty dimension mismatch")
        if units.is_offset(ev["uncertainty"]):
            raise ValueError("uncertainty in an offset unit")
        u = ev["uncertainty"]
        uncertainty = abs(units.base(u) - units.base({"value": 0.0, "unit": u["unit"]}))
    measured, goal = units.base(value), units.base(target)
    relation = req["relation"]
    if relation == "le":
        margin = goal - measured - uncertainty
    elif relation == "ge":
        margin = measured - goal - uncertainty
    elif relation == "eq":
        if req["tolerance"] is None:
            raise ValueError("eq without tolerance")
        # Section 4.3: the tolerance converts as an absolute value (known issue K2).
        margin = abs(units.base(req["tolerance"])) - abs(measured - goal) - uncertainty
    elif relation == "range":
        if req["upper"] is None:
            raise ValueError("range without upper bound")
        margin = min(measured - goal, units.base(req["upper"]) - measured) - uncertainty
    else:
        raise ValueError(f"unsupported relation {relation}")
    return margin >= 0, margin


# --- section 5: evidence, case and requirement assessment ------------------------------------


def may_attest(model: Model, pid: str, role: str) -> bool:
    who = model.principal(pid)
    return who is not None and who["kind"] == "human" and role in who["roles"]


def assess_evidence(
    model: Model, units: Units, req: dict[str, Any], case: dict[str, Any], ev: dict[str, Any]
) -> dict[str, Any]:
    def result(outcome: str, margin: float | None, codes: list[str]) -> dict[str, Any]:
        return {"evidence": ev["id"], "outcome": outcome, "margin_si": margin, "reasons": codes}

    if ev["basis_hash"] != basis_hash(model, req["id"], case["id"]):
        return result("stale", None, ["EVIDENCE_STALE"])
    codes: list[str] = []
    margin: float | None = None
    computed: bool | None = None
    if req["relation"] is not None and ev["value"] is not None:
        try:
            computed, margin = compute(units, req, ev)
        except ValueError:
            return result("invalid", None, ["EVIDENCE_INVALID"])
    method = case["method"]
    quantitative = req["relation"] is not None
    if quantitative and method in "AT" and computed is None:
        codes.append("EVIDENCE_NO_VALUE")
    if method == "A" and quantitative and ev["provenance"] is None:
        codes.append("EVIDENCE_NO_PROVENANCE")
    if method in "AT" and case["tool"] is None:
        codes.append("EVIDENCE_NO_TOOL")
    if method in "RI" or (method == "A" and not quantitative):
        required = ["verifier"]
    elif method == "T":
        required = ["witness"]
    else:
        required = []
    valid = [a for a in ev["attestations"] if may_attest(model, a["principal"], a["role"])]
    if len(valid) != len(ev["attestations"]):
        codes.append("ATTEST_UNAUTHORIZED")
    if any(a["outcome"] == "fail" for a in valid):
        return result("fail", margin, codes + ["ATTEST_FAIL"])
    for role in required:
        if not any(a["role"] == role and a["outcome"] == "pass" for a in valid):
            codes.append("ATTEST_MISSING")
    if computed is False:
        return result("fail", margin, codes)
    return result("incomplete" if codes else "pass", margin, codes)


def assess_case(
    model: Model, units: Units, req: dict[str, Any], case: dict[str, Any]
) -> dict[str, Any]:
    out: dict[str, Any] = {"case": case["id"], "deciding_evidence": None, "evidence": []}
    if approval_state(model, case["id"]) != "approved":
        return out | {"status": "INCOMPLETE", "reasons": ["CASE_NOT_APPROVED"]}
    found = [
        ev for ev in model.items("evidence")
        if not ev["withdrawn"] and ev["case"] == case["id"] and ev["requirement"] == req["id"]
    ]  # fmt: skip
    results = [assess_evidence(model, units, req, case, ev) for ev in found]
    out["evidence"] = results
    if not found:
        return out | {"status": "UNVERIFIED", "reasons": ["NO_EVIDENCE"]}
    live = [(ev, r) for ev, r in zip(found, results, strict=True) if r["outcome"] != "stale"]
    if not live:
        return out | {"status": "STALE", "reasons": ["EVIDENCE_STALE"]}
    ev, decided = max(live, key=lambda pair: (pair[0]["date"], pair[0]["id"]))
    status = {"pass": "VERIFIED", "fail": "FAILED"}.get(decided["outcome"], "INCOMPLETE")
    codes = list(decided["reasons"]) + (["EVIDENCE_FAILED"] if decided["outcome"] == "fail" else [])
    return out | {"status": status, "deciding_evidence": ev["id"], "reasons": codes}


def combine(statuses: list[str]) -> str:
    if not statuses:
        return "UNVERIFIED"
    if "FAILED" in statuses:
        return "FAILED"
    if all(s == "VERIFIED" for s in statuses):
        return "VERIFIED"
    if "STALE" in statuses:
        return "STALE"
    if any(s in ("VERIFIED", "INCOMPLETE") for s in statuses):
        return "INCOMPLETE"
    return "UNVERIFIED"


def assess(model: Model, units: Units) -> list[dict[str, Any]]:
    reqs = {r["id"]: r for r in model.items("requirements")}
    memo: dict[str, dict[str, Any]] = {}

    def one(req: dict[str, Any]) -> dict[str, Any]:
        if req["id"] in memo:
            return memo[req["id"]]
        base = {"requirement": req["id"], "margin_si": None, "derived": [], "cases": []}
        if req["withdrawn"]:
            memo[req["id"]] = base | {
                "verification": "UNVERIFIED", "satisfaction": "WITHDRAWN", "reasons": []
            }  # fmt: skip
            return memo[req["id"]]
        blockers: list[str] = []
        reasons: list[str] = []
        if approval_state(model, req["id"]) != "approved":
            blockers.append("REQ_NOT_APPROVED")
        if req["tbc"]:
            blockers.append("REQ_TBC")
        for assumption in model.items("assumptions"):
            if (
                assumption["status"] == "open"
                and not assumption["withdrawn"]
                and req["id"] in assumption["impacts"]
            ):
                blockers.append("ASSUMPTION_OPEN")
        parts = {p["id"]: p for p in model.items("parts")}
        claims = [
            pid for pid in model.related("satisfy", target=req["id"])
            if pid in parts and not parts[pid]["withdrawn"]
        ]  # fmt: skip
        if not claims:
            blockers.append("NO_DESIGN_CLAIM")
        child_ids = [c for c in model.related("derive", target=req["id"]) if c in reqs]
        children = [one(reqs[c]) for c in child_ids if not reqs[c]["withdrawn"]]
        cases = [
            assess_case(model, units, req, case) for case in model.items("cases")
            if not case["withdrawn"] and req["id"] in model.related("verify", source=case["id"])
        ]  # fmt: skip
        verification = combine([c["status"] for c in cases])
        if not cases:
            if children:
                done = all(c["satisfaction"] == "SATISFIED" for c in children)
                verification = "VERIFIED" if done else "INCOMPLETE"
                reasons.append("BY_DECOMPOSITION")
            else:
                reasons.append("NO_VERIFICATION")
        for c in cases:
            reasons.extend(c["reasons"])
        margins = [
            e["margin_si"] for c in cases for e in c["evidence"]
            if e["margin_si"] is not None and e["evidence"] == c["deciding_evidence"]
        ]  # fmt: skip
        failed = [c for c in children if c["satisfaction"] == "NOT_SATISFIED"]
        open_children = [c for c in children if c["satisfaction"] != "SATISFIED"]
        if open_children:
            reasons.append("CHILD_NOT_SATISFIED")
        if verification == "FAILED" or failed:
            satisfaction = "NOT_SATISFIED"
        elif blockers:
            satisfaction = "BLOCKED"
        elif verification == "VERIFIED" and not open_children:
            satisfaction = "SATISFIED"
        else:
            satisfaction = "OPEN"
        memo[req["id"]] = base | {
            "verification": verification,
            "satisfaction": satisfaction,
            "margin_si": min(margins) if margins else None,
            "derived": child_ids,
            "cases": cases,
            "reasons": blockers + reasons,
        }
        return memo[req["id"]]

    return [one(r) for r in model.items("requirements")]


# --- comparison ------------------------------------------------------------------------------


def _normalize(item: Any) -> Any:
    """Reason codes and derived ids compare as sets; margins compare within a tolerance."""

    if isinstance(item, dict):
        out = {k: _normalize(v) for k, v in item.items()}
        for key in ("reasons", "derived"):
            if key in out:
                out[key] = sorted(set(out[key]))
        return out
    if isinstance(item, list):
        return [_normalize(v) for v in item]
    return item


def _diff(path: str, got: Any, want: Any, out: list[str]) -> None:
    if isinstance(want, float) and isinstance(got, int | float) and not isinstance(got, bool):
        if not math.isclose(got, want, rel_tol=TOLERANCE, abs_tol=TOLERANCE):
            out.append(f"{path}: got {got!r}, want {want!r}")
    elif isinstance(want, dict) and isinstance(got, dict):
        for key in sorted(set(want) | set(got)):
            _diff(f"{path}.{key}", got.get(key), want.get(key), out)
    elif isinstance(want, list) and isinstance(got, list) and len(got) == len(want):
        for i, (g, w) in enumerate(zip(got, want, strict=True)):
            _diff(f"{path}[{i}]", g, w, out)
    elif got != want:
        out.append(f"{path}: got {got!r}, want {want!r}")


def check_vector(data: dict[str, Any], units: Units) -> list[str]:
    model = Model(data["model"])
    want = data["expected"]
    problems: list[str] = []
    hashes = {i: content_hash(model, i) for i in want["content_hashes"]}
    _diff("content_hashes", hashes, want["content_hashes"], problems)
    basis = {}
    for pair in want["basis_hashes"]:
        req_id, case_id = pair.split("|")
        basis[pair] = basis_hash(model, req_id, case_id)
    _diff("basis_hashes", basis, want["basis_hashes"], problems)
    _diff("assessment", _normalize(assess(model, units)), _normalize(want["assessment"]), problems)
    return problems


def check_hashing(data: dict[str, Any]) -> list[str]:
    problems = []
    for case in data["cases"]:
        value = json.loads(case["input"])
        if canonical(value) != case["canonical"] or digest(value) != case["sha256"]:
            problems.append(f"hashing '{case['title']}': got {canonical(value)}")
    return problems


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path("conformance")
    units = Units(json.loads((root / "units.json").read_text(encoding="utf-8"))["units"])
    failures = 0
    problems = check_hashing(json.loads((root / "hashing.json").read_text(encoding="utf-8")))
    print(f"{'FAIL' if problems else 'PASS'} hashing.json")
    for line in problems:
        print("    " + line)
    failures += bool(problems)
    for path in sorted((root / "vectors").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        problems = check_vector(data, units)
        print(f"{'FAIL' if problems else 'PASS'} {data['id']} {data['title']}")
        for line in problems:
            print("    " + line)
        failures += bool(problems)
    print(f"{failures} failing file(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
