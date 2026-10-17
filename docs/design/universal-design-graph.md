# Design: Universal, Agent-Composable Design Graph

Status: revision 2. Section 5 reflects the current plan; phases 1 and 2 are implemented.

## 1. Goal

Given any engineering task, an AI agent can:

1. capture requirements in a generic, verifiable form,
2. discover available capabilities (own rules, CadQuery, FreeCAD, Grasshopper, CalculiX, ...),
3. assemble and validate a graph (a *plan*) from a declarative spec,
4. execute it, with every number traceable to a requirement, an assumption and a tool run,
5. verify requirements with evidence and see what remains open,
6. do all of this standalone (open-source kernels) or by delegating to external CAD/CAE tools.

Non-goals: replacing CAD/CAE kernels, a GUI, general workflow orchestration (Airflow/Dagster scope).

## 2. Current state and gaps

Sound and kept: typed DAG, `@rule` versioning, `Ref`/`Sourced`/`Provisional`, provenance, trial controller, replayable decisions.

| # | Gap | Evidence in code |
|---|-----|------------------|
| G1 | Graph is Python code only; no spec loader | `build_enclosure_graph`, `Graph.add` needs callables |
| G2 | Hardcoded stage vocabulary | `core._STAGE_ORDER` |
| G3 | Closed requirement enum, regex units | `requirements.RequirementKind`, `_UNIT_SCALE` |
| G4 | No requirement-to-check coverage | `verification_matrix` hand-written |
| G5 | Verification is scalar max/min only | `verification.decide` |
| G6 | No tool/adapter boundary; CAD called directly | `geometry.py`, `solver.py` |
| G7 | Pickle cache and pickle hashing | `core._store_cache`, `_canonical` |
| G8 | No units on values | floats everywhere |
| G9 | No agent-facing API | none |
| G10 | No fan-out/map, no iteration record | `Graph` is single-valued DAG |

## 3. Architecture

```
 Agent (LLM)  <--MCP-->  design_graph.mcp  (thin; no logic)
                              |
        +---------------------+----------------------+
        | spec/  GraphSpec (YAML/JSON) -> Graph      |
        | registry/  Capability catalogue            |
        | model/  Requirement, Quantity, Entity      |
        | verify/  Check, Evidence, Coverage         |
        | core/  Graph (DAG, provenance, cache)      |
        | artifacts/  content-addressed store        |
        +---------------------+----------------------+
                              |
              adapters (Capability implementations)
   native rules | CadQuery/build123d | FreeCAD | Grasshopper | CalculiX/Gmsh | ...
```

Layers depend only downward; `core` stays domain-neutral. `domains/enclosure` becomes an example package that registers capabilities and a spec, not the template.

### 3.1 Quantities and units (G8)
- Use `pint` (one shared `UnitRegistry`). `Quantity` Pydantic type = `{magnitude, unit}`, serialized as JSON; stored internally in SI.
- Requirement and capability port types may declare a *dimension* (e.g. `[length]`); validation rejects dimensional mismatch at `check()`.
- Replaces `_UNIT_SCALE` and the regex.

### 3.2 MBSE requirements model (G3, G4)
Requirements are elements of a small system model, aligned with SysML v2 concepts (definition/usage split, typed relationships), not rows in a table. The model is native Pydantic/JSON; SysML v2 JSON/API and ReqIF are import/export mappings, not the internal format.

**Elements** (all have stable `id`, `name`, `doc`, `status`, `version`, metadata):
```
Stakeholder / Need          # source: who wants what, rationale
RequirementDef              # reusable pattern: parameters, constraint expression, dimension
Requirement (usage)         # instance on a subject: RequirementDef + bound target Quantity, tolerance, tbc
Block / Part (system tree)  # subject hierarchy: system > subsystem > component, with Attributes (Quantity)
Constraint                  # typed expression over attributes (le|ge|eq|range|bool|expr)
AnalysisCase                # intent: objective + subject + inputs, realized by a graph subgraph
VerificationCase            # method A|R|I|T + subject + objective, realized by checks/evidence
Assumption                  # explicit, owned, replaces ad-hoc Provisional where it is a modeled fact
```
**Relationships** (typed, directed, first-class; each has id, rationale, status):
```
derive    Requirement -> Requirement   (child derived from parent; allocation of budget)
refine    Requirement/Constraint -> Need or Requirement (adds detail)
satisfy   Part/Design element -> Requirement   (design claims to meet it; evidenced by analysis)
verify    VerificationCase -> Requirement      (how/when it is proven)
allocate  Requirement -> Part          (responsibility)
trace     any -> any                  (informational)
realize   AnalysisCase/VerificationCase -> graph node set (link model to executable graph)
```
**Rules enforced at validation (model checks, not just lint):**
- Every active Requirement has a subject, a bound target (or `tbc`), at least one `verify`, and a `refine`/`derive` ancestor to a Need (traceability up) unless marked a top-level stakeholder requirement.
- Every active Requirement is `satisfy`-ed by at least one Part (traceability down) before a design baseline.
- Every VerificationCase is `realize`-d by graph nodes or manual attestation; every check maps to a VerificationCase.
- Parent/child budget consistency: derived targets must roll up (e.g. allocated masses sum <= parent limit); violations are `DECIDE` items.
- Dimension check: constraint attributes and targets must have matching dimensions (via 3.1).
- No cycles in `derive`/`refine`; withdrawn elements cannot be satisfied or verified.
- Quality lint per INCOSE guidance (singular, unambiguous, verifiable, no escape clauses) on `text`.

**Coverage and status are computed from the model**, not hand-written: per Requirement, derive status from `satisfy` + `verify` + latest check verdict; report gaps (no verify, no satisfy, orphan need, stale after upstream change, provisional-based). `Graph` evaluation populates analysis/verification results; the model references them by node id and artifact hash.

**Execution link:** `AnalysisCase`/`VerificationCase` hold a `GraphSpec` fragment or reference (3.5). The agent edits the model (add need, derive requirement, allocate, define case), then the system generates/validates the executable graph from it.

**Persistence and interchange:**
- Source of truth: model files in YAML/JSON (diffable, versioned in Git), one element per entry with stable ids.
- Export/import: ReqIF (existing `requirement_io` kept, mapped to Requirement + Need), SysML v2 JSON (per the OMG SysML JSON schema, requirement/part/relationship subset), CSV for review.
- Existing `RequirementCard` loads through a compatibility mapper (kind -> RequirementDef; card -> Requirement on the system subject).

### 3.3 Capabilities and adapters (G1, G6, G9)
```
Capability:                      # what the agent can discover
  id, version, description
  inputs:  {port: PortSpec}      # JSON Schema + optional dimension
  outputs: {port: PortSpec}
  kind: compute | geometry | mesh | solve | check | io
  fidelity_tier: int, validity: {param: range}
  cost: {time_s_hint}, availability(): ok | missing(reason)
  determinism: pure | seeded | external
  side_effects: none | writes_artifacts | external_app
```
- `@rule` becomes sugar that registers a native `Capability`; existing rules stay valid.
- `Adapter` Protocol: `probe()`, `run(inputs, workdir, timeout) -> outputs + artifacts`. Adapters never share in-process objects across the boundary; they exchange **artifacts** (STEP, BREP, STL, mesh, JSON results).
- Concrete adapters, in order: CadQuery (extract from `geometry.py`), FreeCAD headless, Grasshopper (Rhino.Compute/MCP), Gmsh + CalculiX. Each is an optional extra and skipped cleanly when absent.
- Fidelity ladder reuses `NEEDS-HIGHER-TIER`: a check whose result is out of a capability's validity range names the next tier capability.

### 3.4 Artifacts and caching (G7)
- `ArtifactRef {uri, sha256, media_type, bytes, produced_by}` in a content-addressed store under `.design_graph/artifacts/`.
- Cache keys hash canonical JSON of inputs plus artifact SHA-256s, plus capability id/version and adapter tool version. Pickle is removed; values must be Pydantic/JSON-serializable or artifact refs. Unhashable values are an error, not a pickle fallback.
- Cache entries stored as JSON.

### 3.5 Graph spec (G1, G2, G10)
```yaml
spec_version: 1
domain: enclosure
nodes:
  - id: plate_freq
    capability: native.plate.simply_supported_frequency@1
    inputs: {model: {ref: plate_model}}
    stage: analysis
  - id: loads_map
    map: {over: {ref: load_cases}, capability: ..., as: case}   # fan-out
requirements: [...]
checks:
  - id: chk_freq
    covers: [REQ-12]
    capability: native.check.compare@1
    inputs: {value: {ref: plate_freq.f1}, requirement: REQ-12}
```
- `Graph.from_spec(spec, registry)` resolves capabilities, builds nodes, runs the existing `check()` (type and path validation).
- Stages become an optional per-domain ordered list in the spec; default is none (dependency order only).
- `map` nodes evaluate one capability per item with per-item provenance.
- `Graph.to_spec()` round-trips so agent edits can be diffed and versioned.

### 3.6 Verification and coverage (G4, G5)
- `Check` = capability producing `CheckResult {requirement_ids, verdict, margin, evidence}`; `decide()` generalized to le/ge/eq-with-tolerance/range/bool; uncertainty handling unchanged.
- `Evidence` = graph value ref | `ArtifactRef` | manual attestation (A/R/I/T method, signer, date). Review/Inspection/Test items stay `OPEN` until attested.
- **Coverage report**: every active requirement must be covered by at least one check; uncovered, orphan checks, failing, provisional-based, and stale (upstream changed) results are listed. Unverified never reads as PASS.
- Existing `replay()` determinism property is kept for all relations.

### 3.7 Workflow layer (flow of actions)
- `Plan` = ordered/partial-order list of `Step {goal, capability or subgraph, preconditions, expected effects}`; it is a thin layer that *produces* a GraphSpec, not a second executor.
- Iteration (design loops) is an outer loop that records `Trial {bindings, results, accepted}`; the DAG stays acyclic. `SizingController` generalizes to N levers with a pluggable strategy (greedy now, optimizer hooks later: variables, objectives, constraints declared in spec).

### 3.8 Agent interface (G9)
MCP server (`design-graph-mcp`), tools:
`list_capabilities`, `describe_capability`, `validate_spec`, `evaluate`, `explain_node`, `get_verification_status`, `list_open_items`, `set_binding`, `add_assumption`.
- Every tool has `inputSchema` and `outputSchema`; results returned as `structuredContent`; validation failures are `isError: true` with machine-readable codes (node, parameter, expected, got) rather than prose.
- Mutating/external-app tools are annotated, and long runs have timeouts. No tool executes arbitrary code or shell strings from the agent; adapters run fixed commands with argument lists.

## 4. Best-practice review

Verified against sources fetched during this review:

| Source | Practice | Applied |
|--------|----------|---------|
| MCP Tools spec (2025-06-18) | `inputSchema`/`outputSchema`; `structuredContent` plus text fallback; protocol errors vs `isError` tool errors; validate inputs, timeouts, audit logging, human-in-loop for sensitive ops | 3.8 |
| W3C PROV overview | Provenance = entities, activities, agents; supports reproducibility, versioning, derivation; validators from constraints | 3.4, 3.5: runs are activities, artifacts are entities, adapters/tool versions recorded as agents; `ProvenanceRecord` gains tool version and artifact hashes |
| OMG SysML v2 (formal 2.0, 2025) and KerML | MBSE language covering requirements, structure, behavior, analysis and verification cases; JSON schema, Quantities & Units and Requirement Derivation libraries, Systems Modeling API | 3.2: definition/usage, derive/refine/satisfy/verify/allocate relationships, analysis/verification cases, JSON interchange mapping |
| Pint docs | Registry-based unit handling instead of ad-hoc tables | 3.1 |
| OMG ReqIF 1.2 | Requirements interchange via typed attributes and relations | 3.2 keeps ReqIF import mapping to generic model; link types (parent/derived) mirror ReqIF relations |

Could not be fetched (HTTP 404/not found), so applied from general knowledge and flagged for your confirmation:
- INCOSE Guide to Writing Requirements: quality characteristics and a lint pass (3.2).
- OpenMDAO group/connection model and unit-checked connections: motivates typed ports and dimension checks (3.1, 3.3).
- Content-addressed storage (Git/Nix/Bazel style): hash-keyed caches and hermetic inputs (3.4).
- OSLC linking and INCOSE requirement-derivation practice. The full SysML v2 metamodel is not adopted; a subset mapping is exported (3.2).

Risks and decisions:
- R1: Over-engineering. Mitigation: phased delivery; each phase keeps existing tests green.
- R2: Breaking API. Mitigation: `@rule`, `Graph.add`, `RequirementCard` kept as compatible front doors.
- R3: External tools not installed in CI. Mitigation: `probe()` + skipped tests; native fallbacks.
- R4: Agent-generated specs are untrusted input. Mitigation: validation only references registered capabilities; no import paths or code in specs.
- D1: Adopt `pint` and `jsonschema`/Pydantic schema export (new deps: `pint`; MCP SDK as optional extra `mcp`).

## 5. Plan (revision 2)

Decisions that supersede earlier text:
- **Toolchain credibility is the engineer's responsibility.** The engineer preselects CAD/CAE tools and declares them (`ToolDeclaration`: name, version, intended use, declared by). The framework records the declaration and ties evidence to it; it does not assess solver credibility (findings F1, F4, F12 are deferred, not dropped). Credibility gating may be added later as an optional policy.
- **Priority is requirement satisfaction and verification assessment.** Status is derived by rules from the model and evidence, never asserted by whoever writes the model (F2, F3, F6, F8).
- `pint` is adopted; pickle cache removal, CadQuery-then-FreeCAD order, and repo-YAML attestations follow the defaults from section 6.

Phases (each ends green: pytest, ruff, mypy):
1. **Repo restructure** (done): `core/`, `model/`, `engineering/`, `interchange/`, `domains/`.
2. **Base MBSE model and assessment** (this iteration): units (`pint`), elements (Need, Part, Requirement, VerificationCase, Assumption, Evidence, ToolDeclaration, Principal, Approval), typed relationships, YAML store, validation rules, derived verification and satisfaction status with margins, baselines with change impact, graph-to-evidence bridge, CLI `dg model validate|assess`.
3. **Model extensions** (started: RequirementCard importer, verification matrix, solver EPERM fix; verification `level`/`stage` fields exist): interfaces/flows, modes/scenarios, variants, verification level/stage, budgets with contingency, hazard links, RequirementCard compatibility loader, SysML v2 JSON / ReqIF mapping.
4. **Artifacts and cache**: `ArtifactRef`, content-addressed store, JSON cache, remove pickle.
5. **Capability registry and graph spec**: `Capability`, `GraphSpec`, `from_spec`/`to_spec`, `map` nodes, configurable stages.
6. **Adapters**: CadQuery extraction, FreeCAD, Grasshopper, CalculiX/Gmsh, with `probe()` and environment fingerprints.
7. **MCP server**: read and propose tools only; approvals and attestations are never exposed to the agent.
8. **Port the enclosure example** to the model and spec.

### 5.1 Assessment rules implemented in phase 2
- Elements are approved by a **human principal**; an approval pins the element's content hash, so any edit invalidates it. Agent principals cannot approve or attest (enforced by validation).
- A requirement's status is computed from: its approval, TBC flag, open assumptions, design `satisfy` claims, `verify`-linked cases (conjunctive), current evidence, and the status of requirements derived from it.
- **Evidence outcome is computed, not stated.** Analysis and test evidence carry a measured value with unit and uncertainty; pass/fail and margin are calculated against the requirement's relation, target and tolerance with dimensional checking. Review and inspection need a human attestation. Analysis evidence must carry graph provenance and a declared tool; test evidence also needs a human witness attestation.
- Evidence is bound to a **basis hash** of the requirement, case, subject part and tool; if any change, the evidence is `STALE` and does not count.
- Verification status: `VERIFIED`, `FAILED`, `STALE`, `INCOMPLETE`, `UNVERIFIED`. Satisfaction status: `SATISFIED`, `NOT_SATISFIED`, `OPEN`, `BLOCKED` (with machine-readable reasons), `WITHDRAWN`. Nothing is `SATISFIED` unless approved, claimed by a part, verified with current passing evidence, free of open assumptions and TBC, and all derived children are satisfied.
- Baselines record element hashes; `changes_since` reports changed elements and the requirements affected.
- Known limit: principals are declared in the model file and not authenticated. Use signed commits and CODEOWNERS on the model directory until a real identity layer exists.

## 6. Open questions
1. Which external tool first after CadQuery: FreeCAD or Grasshopper?
2. Should credibility gating (optional policy) be added after phase 6?

## 7. Engineering-assurance review

Reference frames: ISO/IEC/IEEE 15288 and 29148 (lifecycle, requirement quality), ECSS-E-ST-10-02C and NASA SE Handbook (verification and validation, verification matrix, closure), NASA-STD-7009B (model credibility, V&V, uncertainty, sensitivity; scope confirmed from the NASA standards page), ASME V&V 10/20 (code and solution verification, validation, UQ), JCGM 100 GUM (uncertainty), ISO 26262-8 / DO-330 (software tool confidence). Only NASA-STD-7009B was fetched; the others are applied from general knowledge and should be checked by a domain reviewer.

### 7.1 Verdict
- As a **traceability and provenance system** the design would withstand scrutiny: typed links, hashed inputs, replayable decisions, explicit assumptions.
- As a **producer of credible numerical results** the current design would not: it proves a calculation was run consistently, not that it is correct, applicable, or sufficiently accurate. A reviewer would reject a "PASS" built only on what is in sections 3.1 to 3.8.
- With the changes below, results become defensible as decision-support evidence. They are not certification evidence until the toolchain is qualified for the specific domain and process.

### 7.2 Findings and required changes

| # | Severity | Finding | Required change |
|---|----------|---------|-----------------|
| F1 | Critical | Verification checks a number, not the model that produced it. No code verification (solver solves equations right), solution verification (mesh/step convergence), validation (agrees with test/reference), or UQ. `decide()` has one scalar `uncertainty`. | Add `ModelCredibility` per capability and per run: V&V evidence refs, validated domain, convergence result, uncertainty (standard + coverage factor per GUM), sensitivity, known limitations, aligned to NASA-STD-7009B credibility factors. A PASS needs credibility at or above the level declared for the requirement's criticality; otherwise verdict is `NEEDS-HIGHER-TIER` or `OPEN`. |
| F2 | Critical | `satisfy` is a claim link. A requirement could read "satisfied" without evidence. | Derive satisfaction only: satisfied iff a `verify`-linked case passed, on the current design baseline, with credible evidence, with no open provisional dependency. `satisfy` links are intent only. |
| F3 | Critical | Author/verifier independence. The agent that builds the model also builds the checks and could attest R/I/T or mark items closed. | Separation of duties: agent may not set attestation, approve baselines, close TBC/TBR, or waive failures. Attestations require an identified human principal (signed or authenticated, not free text). Independent re-run capability (second capability or tier) for safety-critical checks. |
| F4 | High | Validity of native analytic rules is implicit (e.g. thin-plate, simply-supported assumptions). Declared `validity` ranges have no owner or evidence. | Each capability declares applicability predicates (e.g. h/a limit, boundary-condition class) evaluated at run time; out-of-applicability is a failure, not a warning. Ranges cite a source and are covered by golden tests against reference solutions (extend `scripts/benchmark_analytical.py`). |
| F5 | High | Margin definition is ad hoc (`limit - value - u`). No factors of safety, no confidence level, no margin-of-safety convention, no load/environment combinations. | Explicit `MarginPolicy` per requirement: factor of safety, MoS convention (ratio form), confidence, uncertainty combination. Load cases and environments are first-class model elements. |
| F6 | High | Requirements validation is missing. The model verifies against requirements but never checks that requirements are right for the Needs. LLM-authored requirements may be fluent but wrong. | Need and Requirement states `draft -> reviewed -> approved`; only approved requirements count toward baselines. Validation case type (against Need) alongside verification. Agent output is `draft` by default. |
| F7 | High | Reproducibility of external tools. Hashing inputs is not enough: solver version, OS, floating-point, mesh seeds, settings. | Record an environment fingerprint (tool + version + platform + config) per run; `determinism: external` runs are cached by fingerprint and re-run on drift. Lock native dependencies. Provide a `reproduce` command that compares results within a stated tolerance. |
| F8 | High | Baselines and change control. Git history is not a controlled baseline; staleness relies on hash changes. | Explicit `Baseline` (content hash of model + capability versions + artifact hashes), approval record, impact analysis (which requirements/results are stale when an element changes), and a verification closure record per baseline. |
| F9 | Medium | MBSE model is missing elements engineers expect: interfaces and flows (ICDs, ports, interface requirements), behavior/modes/scenarios, configurations/variants, verification level/stage (component, assembly, system), hazards/FMEA links. The user's "flow requirements" is only partially covered by derive/allocate. | Add `Interface` / `Port` / `Flow` elements with interface requirements; `Mode`/`Scenario`; `Variant`; verification `level` and `stage` on cases (ECSS-style verification matrix); optional `Hazard` linked to requirements. |
| F10 | Medium | Budget roll-up checks sums only; no maturity-based contingency or margin growth. | `Budget` element with allocation, maturity class, contingency policy, and roll-up with uncertainty. |
| F11 | Medium | Units: dimensional agreement does not ensure correctness. Offset units (degC), angles, coordinate frames, geometric tolerances, and axis conventions can still be wrong. | Reject ambiguous offset-unit arithmetic; require `Frame` on vector/geometry quantities; carry tolerance as part of the quantity; conversions logged in provenance. |
| F12 | Medium | Toolchain confidence. Nothing shows the framework itself is correct. | Tool-confidence record (ISO 26262-8 style TCL reasoning): regression suite with reference solutions, property tests for the validation rules, mutation-style negative tests (a known-wrong model must FAIL). Publish a statement of intended use and limits. |
| F13 | Medium | Interchange loss. SysML v2 / ReqIF subset mapping may silently drop semantics. | Round-trip tests; export declares what was dropped; import into the model never overwrites approved elements. |
| F14 | Low | Provisional/assumption handling: `Provisional` is lightweight. | Assumption element with owner, rationale, expiry, closure criteria, and impact list; open assumptions block baseline approval. |
| F15 | Low | LLM nondeterminism leaks into the record. | Log agent proposals as proposals (who/what/prompt hash); only validated, approved spec changes enter the model. |

### 7.3 Resulting plan changes
- Add **Phase 0 (assurance primitives)** before Phase 1: `ModelCredibility`, `MarginPolicy`, approval/principal model, `Baseline` and impact analysis, status derivation rules (F1, F2, F3, F5, F8). Phase 1 and later phases build on these.
- Phase 1/1b extended with F6, F9, F10, F11, F14.
- Phase 4 verification generalization adds credibility gating and independent re-run.
- Phase 5 adapters add environment fingerprints and applicability predicates (F4, F7).
- New **Phase 8 (assurance case)**: tool-confidence record, benchmark/golden suite, statement of intended use (F12, F13).
- Acceptance criterion for the whole project: a deliberately wrong design, a deliberately wrong model, and a deliberately unverified requirement must each be caught and never reported as PASS.

### 7.4 What remains the human's job
Choosing criticality and credibility thresholds per requirement, approving requirements and baselines, performing or witnessing R/I/T, accepting residual risk, and any regulatory qualification. The tool records and enforces these; it must not decide them.
