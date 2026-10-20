# Conformance: verification and invalidation

The ontology in `ontology/` lets another system read a design graph. The files here let it
reach the same **verdicts**: which evidence is stale, which approvals still hold, and whether
each requirement is SATISFIED, NOT_SATISFIED, OPEN or BLOCKED.

| File | Contents |
|---|---|
| [verification-semantics.md](verification-semantics.md) | The rules: canonical JSON, content and basis hashes, approval state, units and margins, assessment, known issues |
| `vectors/Vnn-*.json` | 23 models, each with expected hashes and assessment |
| `hashing.json` | Canonical-JSON test cases (key order, floats, escaping) |
| `units.json` | Conversion of each unit the vectors use to coherent SI base units |
| `independent/check.py` | A checker written against the spec, using only the Python standard library |

The vectors are abstract: one subject `SYS` with attributes `x`, `t`, `l` and `b`, requirements
`R0`–`R7` written as plain constraints (`SYS shall have x >= 3.`), cases `C1`–`C3`, a tool
`T1`, a human principal `H1` and an agent `A1`. Values are dimensionless except in the four
vectors that test unit rules (V04–V07, using `m`, `mm`, `degC` and `delta_degC`). The vectors
cover pass, fail, uncertainty,
unit conversion and offset units, staleness after editing the requirement or the tool,
approvals made stale by an edit, ignored agent approvals and attestations, latest-evidence
selection, open assumptions and verification through derived requirements. Each vector lists
the [Engineering Process Specification](https://github.com/burhop/engineering-process-spec)
clauses it relates to (`EPS P12`, `EPS C10`, ...).

## Use

Check an implementation: read each vector's `model`, compute the hashes and the assessment,
and compare them with `expected` as described in section 6 of the spec. The included checker
shows how:

```sh
python -I conformance/independent/check.py conformance
```

Regenerate the vectors after an intended behaviour change; a test fails if they are out of
date:

```sh
python scripts/build_conformance.py
```

The expected values come from `design_graph` itself. The checker reproducing them shows that
the spec is complete enough to implement from. It was written by the same project, though, so
it is not independent evidence in the sense of the EPS conformance plan; a checker in another
language, written by someone else, would be.
