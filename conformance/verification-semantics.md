# Verification semantics

Status: draft 0.1 (suite `design-graph-verification/0.1`), October 2026. This document describes
what `design_graph` 0.3.0 does. The vectors in `vectors/` are the test. If this text and the
vectors disagree, the vectors are right and this text has a bug.

The ontology in `ontology/` defines the vocabulary and the structure (SHACL). It does not define
how hashes are computed, when evidence goes stale, or how a requirement's status follows from its
evidence. This document defines those three things so that another implementation can reproduce
them. `independent/check.py` implements this document with the Python standard library only.

## 1. Input: the normalized model

The input is the **normalized model**, the JSON object `SystemModel.model_dump(mode="json")`
writes. In this form every collection and every field is present, including default values
(`"name": ""`, `"withdrawn": false`, `null` for an absent optional value). Collections are lists.
The element types are listed in this order:

`principals, tools, needs, parts, requirements, cases, assumptions, relationships, evidence,
approvals, baselines, statements, design_rules, concepts, trade_studies, load_cases,
failure_modes, optimizations, allowables, credibility, reviews, consistency_checks`

Element ids are unique across all collections; `dg model validate` checks this. Lookup by id
searches the collections in this order.

Field names are the snake_case names of the normalized model. The RDF export renames fields to
camelCase, drops list order and writes no hashes for the current content. Hashes therefore
cannot be computed from the RDF (see K1).

**Integers and floats are different values.** A field typed as a float (every `Quantity.value`,
for example) always has a fraction or an exponent in the normalized model (`3.0`, not `3`). A
reader must keep that distinction: `2.0` and `2` hash differently. JSON parsers that turn every
number into one numeric type (JavaScript's `JSON.parse`) need the field types, or a lexical
parser, to keep it.

## 2. Canonical JSON and digest

`digest(v)` is the lowercase hex SHA-256 of `canonical(v)` encoded as ASCII.
`canonical(v)` is the JSON text with:

- object keys sorted by Unicode code point, at every level;
- no whitespace: `,` between items and `:` between key and value;
- list order kept exactly;
- `true`, `false` and `null` written as literals; integers in decimal without a sign for
  positive values;
- strings in double quotes. `"` and `\` are escaped with a backslash; backspace, form feed,
  newline, carriage return and tab are written `\b \f \n \r \t`; every other character outside
  U+0020–U+007E (including U+007F) is written `\uXXXX` in lowercase hex. Characters above U+FFFF
  are written as their UTF-16 surrogate pair (`🔥`);
- floats written as the **shortest digit string that reads back to the same IEEE-754 double**.
  With the decimal exponent `e` of that digit string written in scientific form (`d.ddd × 10^e`):
  - if `-4 <= e < 16`, use fixed notation with at least one digit after the point
    (`2.0`, `0.1`, `0.0001`, `1000000000000000.0`);
  - otherwise use `d.ddde±XX` with a sign and at least two exponent digits, dropping the point
    when there is one digit (`1e-05`, `1e+16`, `1.2345678901234568e+17`);
  - negative zero is `-0.0`. NaN and infinity are not allowed.

These are the rules of Python's `json.dumps(v, sort_keys=True, separators=(",", ":"))`.
`hashing.json` has test cases for each rule.

## 3. Content hash, basis hash and approval state

**Content hash.** For an element with id `x`:

```
content_hash(x) = digest({"content": <element x as in the normalized model>})
```

For a verification case, the payload also gets
`"verifies": sorted(ids of the requirements that active verify relationships from x point to)`.
That way, changing which requirements a case verifies also changes the case.

A relationship is **active** when `withdrawn` is false. Withdrawn endpoints do not make a
relationship inactive.

**Basis hash.** The basis of evidence for requirement `r` and case `c`:

```
basis_hash(r, c) = digest({
  "requirement": content_hash(r),
  "case":        content_hash(c),
  "subject":     content_hash(r.subject) if that element exists, else "",
  "tool":        content_hash(c.tool) if c.tool is set and that element exists, else ""
})
```

When evidence is recorded, its `basis_hash` is stored. If the stored value differs from the
current `basis_hash(evidence.requirement, evidence.case)`, the evidence is **stale**. Editing
the requirement, the case, its verify links, the subject part (including its design attributes)
or the declared tool (for example its version) makes it stale (V08, V10).

**Approval state** of element `x`. Go through `approvals` in list order and skip approvals that
are withdrawn, that are for another element, or whose principal is missing, is not `human` or
lacks the `approver` role. If a remaining approval's `content_hash` equals `content_hash(x)`,
the state is `approved`. Otherwise it is `stale` if there was at least one remaining approval,
and `none` if there was none. An agent approval therefore never counts (V11), and an edit
without re-approval leaves the approval stale (V09).

## 4. Quantities

### 4.1 Units

A quantity is `{"value": float, "unit": string}`; the empty unit is dimensionless.
`units.json` lists every unit the vectors use, with:

```
base(q) = q.value * factor + offset          # coherent SI base units (kg, m, s, K, ...)
```

and a dimension (exponents of mass, length, time, temperature, ...). Two quantities have the
same dimension when their dimension maps are equal. A unit is an **offset unit** when its
offset is non-zero (`degC`). `design_graph` parses units with pint and accepts more units than
the table lists; the table is the subset this suite pins down.

### 4.2 Margin

For requirement `r` and evidence `e` with `r.relation` set and `e.value` not null:

- `bool`: `e.value` must be a boolean. Passes when `e.value == r.expected`; no margin.
- Otherwise `e.value` must be a quantity, `r.target` must be set, and they must have the same
  dimension. If `e.uncertainty` is set, it must have that dimension too and must not use an
  offset unit; `u = |base(e.uncertainty)|`, otherwise `u = 0`. With `m = base(e.value)` and
  `t = base(r.target)`:

| relation | margin |
|---|---|
| `le` | `t - m - u` |
| `ge` | `m - t - u` |
| `eq` | `\|base(r.tolerance)\| - \|m - t\| - u` (tolerance required) |
| `range` | `min(m - t, base(r.upper) - m) - u` (upper required) |

The evidence passes when `margin >= 0`. The margin is reported in coherent SI base units
(`margin_si`). If any "must" above fails, the evidence is **invalid**.

### 4.3 Floating point

Margins are computed in IEEE-754 double precision, after unit conversion. A value exactly at
the boundary in a converted unit may land on either side of zero, depending on how the
implementation multiplies. The vectors avoid such values; compare margins with a relative and
absolute tolerance of 1e-9.

## 5. Assessment

Assessment needs unique ids and no `derive` cycles; it does not otherwise require the model
to pass validation. Each vector records the validation error codes separately (V05, V11 and
V12 have some); the assessment does not depend on them.

### 5.1 Evidence outcome

For evidence `e` of requirement `r` under case `c` (method `A` analysis, `R` review,
`I` inspection, `T` test). Steps run in order and the first `return` wins.

1. `e.basis_hash != basis_hash(r, c)` → return **stale** [`EVIDENCE_STALE`].
2. If `r.relation` is set and `e.value` is not null, compute pass/fail and margin (4.2). If it
   is invalid → return **invalid** [`EVIDENCE_INVALID`].
3. Collect reasons:
   - `EVIDENCE_NO_VALUE` if `r.relation` is set, the method is `A` or `T`, and no pass/fail was
     computed;
   - `EVIDENCE_NO_PROVENANCE` if the method is `A`, `r.relation` is set and `e.provenance` is
     null;
   - `EVIDENCE_NO_TOOL` if the method is `A` or `T` and `c.tool` is null.
4. Required attestation role: `verifier` for `R` and `I`, and for `A` when `r.relation` is
   null; `witness` for `T`; none otherwise.
5. An attestation is valid when its principal exists, is `human` and holds the attested role.
   If any attestation is not valid, add `ATTEST_UNAUTHORIZED` and ignore it (V12).
6. If a valid attestation has outcome `fail` → return **fail** [`ATTEST_FAIL`, plus the
   reasons so far].
7. If no valid attestation has the required role with outcome `pass`, add `ATTEST_MISSING`.
8. If pass/fail was computed as fail → return **fail** (with the reasons so far).
9. If there are any reasons → **incomplete**; otherwise → **pass**.

### 5.2 Case status

For case `c` and requirement `r`:

1. If the approval state of `c` is not `approved` → **INCOMPLETE** [`CASE_NOT_APPROVED`]. No
   evidence is assessed (V23).
2. Take the evidence that is not withdrawn and has `case == c` and `requirement == r`, in list
   order, and assess each one (5.1). None → **UNVERIFIED** [`NO_EVIDENCE`].
3. If all of it is stale → **STALE** [`EVIDENCE_STALE`].
4. Otherwise, the **deciding evidence** is the non-stale item with the greatest `(date, id)`,
   comparing both as strings (see K3). Its outcome maps to the status: pass → **VERIFIED**,
   fail → **FAILED** [adds `EVIDENCE_FAILED`], incomplete or invalid → **INCOMPLETE**. The case
   takes the deciding evidence's reasons. Older results are kept and reported, but they do not
   decide (V14).

Several case statuses combine as: none → UNVERIFIED; any FAILED → FAILED; all VERIFIED →
VERIFIED; any STALE → STALE; any VERIFIED or INCOMPLETE → INCOMPLETE; otherwise UNVERIFIED.

### 5.3 Requirement status

A withdrawn requirement is UNVERIFIED and **WITHDRAWN**, with no cases and no reasons.
Otherwise:

- **Blockers:**
  - `REQ_NOT_APPROVED` if its approval state is not `approved`;
  - `REQ_TBC` if `tbc`;
  - `ASSUMPTION_OPEN` for each open, non-withdrawn assumption whose `impacts` lists it;
  - `NO_DESIGN_CLAIM` if no non-withdrawn part is the source of an active `satisfy` to it.
- **Derived requirements**: the sources of active `derive` relationships that target it and
  are requirements. Non-withdrawn ones are assessed recursively. The model must have no derive
  cycles; validation reports them.
- **Cases**: the non-withdrawn cases that are the source of an active `verify` to it, assessed
  as in 5.2. Verification is the combination of their statuses. If there are no cases, then:
  with derived requirements, it is VERIFIED if all of them are SATISFIED and INCOMPLETE
  otherwise [`BY_DECOMPOSITION`]; with no derived requirements, it is UNVERIFIED
  [`NO_VERIFICATION`].
- **Reasons** = the blockers, plus the reasons of every case, plus `CHILD_NOT_SATISFIED` for
  each derived requirement that is not SATISFIED.
- **Margin** = the smallest margin of any case's deciding evidence, or null.
- **Satisfaction**, the first that applies:
  1. verification is FAILED, or a derived requirement is NOT_SATISFIED → **NOT_SATISFIED**;
  2. any blocker → **BLOCKED**;
  3. verification is VERIFIED and every derived requirement is SATISFIED → **SATISFIED**;
  4. otherwise → **OPEN**.

## 6. Vector format

Each `vectors/Vnn-*.json` has `suite`, `id`, `title`, `description`, `references` (clauses of
the [Engineering Process Specification](https://github.com/burhop/engineering-process-spec)
draft that the vector exercises), `model` (normalized) and `expected`:

- `validation_errors`: sorted error codes from `dg model validate` (informative);
- `content_hashes`: id → content hash, for every element;
- `basis_hashes`: `"requirement|case"` → basis hash, for every verify link and every evidence
  pair;
- `assessment`: one entry per requirement, in model order, with `verification`,
  `satisfaction`, `margin_si`, `derived`, `reasons` and `cases`; each case has `status`,
  `deciding_evidence`, `reasons` and `evidence` (`outcome`, `margin_si`, `reasons`).

`reasons` and `derived` are compared as sets, and margins with the tolerance in 4.3. Human-
readable messages are not part of the contract.

## 7. Known issues

These are behaviours of 0.3.0 that the vectors record but a future version may change.

- **K1. The RDF export cannot carry invalidation.** Hashes are defined over the normalized
  JSON. The Turtle and JSON-LD exports rename fields, drop list order and omit the current
  hashes, so an RDF-only consumer can read `dg:basisHash` but cannot decide whether evidence is
  stale. Possible fixes: export each element's current content hash and the hash payload
  version, or define the hash over an RDF canonicalization (RDFC-1.0).
- **K2. Tolerance converts as an absolute value.** `eq` uses `|base(tolerance)|`, which is
  wrong for an offset unit (a `degC` tolerance becomes kelvin). Validation rejects offset
  tolerances (`REQ_OFFSET`), so this only affects invalid models.
- **K3. Evidence dates compare as strings.** Any ISO 8601 form Python accepts is allowed
  (`2026-10-02`, `20261002`, `2026-10-02T09:00`), but ordering uses string comparison. Mixing
  forms can choose the wrong deciding evidence: `20260101` sorts after `2026-12-31`, because
  `1` sorts after `-`. Normalizing dates, or restricting them to one form, would fix it.
- **K4. Validation accepts an offset-unit uncertainty.** `dg model validate` reports no error
  when evidence uncertainty is in `degC`; assessment treats that evidence as invalid (V07).
- **K5. The hash is Python's JSON dialect.** It is reproducible (section 2, `hashing.json`) but
  not a published canonicalization. Moving to RFC 8785 (JCS) would change every hash and make
  every existing approval stale, so that change needs a hash version field and a migration.
