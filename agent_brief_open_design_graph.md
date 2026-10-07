# Agent brief: build an open-source design-intent graph for engineering design

You are building, from scratch, an open-source Python library and CLI that designs, verifies and sizes engineering
artefacts through a **design-intent graph**: every derived value is a node with typed inputs, provenance and declared
assumptions, wired from requirements through rules and geometry to analyses and verification. The reference
application is a machined enclosure for a printed circuit board, but the graph core must be domain-neutral.

This brief comes from an internal prototype that proved the approach. You do not have that code and must not try to
obtain it. Build a clean implementation from this description.

---

## 0 Hard constraints

- **Licence:** Apache-2.0. Add a `LICENSE` file and SPDX headers.
- **Allowed dependencies (permissive):**
  - pydantic, networkx, numpy, scipy, PyYAML, click
  - CadQuery / OCP (Apache-2.0)
  - matplotlib, openpyxl
  - pdfplumber / pdfminer.six (MIT)
  - Playwright (Apache-2.0), FastAPI/uvicorn, three.js
- **GPL tools only as separate programs or optional extras:**
  - gmsh: optional extra, document the GPL consequence
  - CalculiX, openEMS: external executables only
- **Do not use:**
  - PyMuPDF or any other AGPL package
  - Code copied from proprietary sources
- **No proprietary or customer data, anywhere:**
  - All examples, fixtures and test documents are invented by you.
  - Do not imitate any real company's document format, headers, classification markings or requirement-ID schemes.
  - Standards (ECSS, MIL, IEEE) are referenced by number and clause only; never reproduce their text.
- **Python ≥ 3.12.** Typed code, with `pydantic` models for every value that crosses a node boundary.
- **Every feature comes with tests on synthetic data.** Solver-backed tests are marked `slow`.

---

## 1 What the graph is (core semantics — get these exactly right)

### 1.1 Nodes and bindings
- A **node** is a named, pure function plus a map of parameter → **binding**.
- A binding is one of:
  - a constant
  - a `Ref(node, path="")`: the output of another node, optionally a field path into it, e.g.
    `Ref("design_inputs", "f_max_Hz.value")`
  - a `Provisional(value, reason)`: a constant that is an assumption
  - a list/tuple containing the above
- Functions are registered with a decorator carrying metadata:

  ```python
  @rule(version="2", source="method / standard clause / doc section this rule implements")
  def lid_seam(layout: Layout, t_wall: float, f_max_Hz: float, pitch_max: float = 25.0) -> SeamDesign: ...
  ```

  - `version` must be bumped whenever behaviour changes; it is part of the cache key.
  - `source` is the human citation shown in reports and the graph view.

### 1.2 Typed ports and wiring checks
- Parameter annotations are the input types; the return annotation is the output type.
- `Graph.check()` runs before evaluation. It fails with a precise message when:
  - a `Ref` points to an unknown node
  - a path cannot exist on the producing type (resolve paths against pydantic field annotations)
  - the produced type does not match the consumer's annotation
- No cycles. The graph is a DAG; loops live outside it (§1.7).

### 1.3 Evaluation, caching, identity
- Evaluate in topological order, optionally only the ancestors of requested targets.
- **Cache key** = content hash of:
  - the function's qualified name and its version
  - the fully resolved input values (canonical JSON of pydantic models; numpy arrays by bytes)

  The same inputs must never recompute.
- Two-level cache: in memory, plus an optional on-disk pickle cache. Values that cannot be pickled (e.g. B-rep shapes)
  stay in memory only, silently.
- **External artefacts** (solver input decks, result files) are keyed the same way:
  - Hash the written input file and record `<job>.solved` with the hash after a successful run.
  - A re-run with an identical deck reuses the results without starting the solver.
  - The prototype lost 12 h to a re-solve before this existed.

### 1.4 Provenance
Every evaluation records:
- node, function, version, source
- input hash, upstream node names
- cache hit (yes/no), wall time
- the provisional reasons in effect

Provenance is queryable (`g.provenance(name)`) and serialised with the graph.

### 1.5 Provisional propagation (assumption tracking)
- A node is **provisional** if any of these holds:
  - one of its bindings is `Provisional`
  - an upstream node is provisional
  - the function itself returns `Provisional(value, reason)`, e.g. "unreviewed library entry", "fallback used"
- Reasons are strings, propagated downstream (union, deduplicated) and never dropped silently.
- Reports and the verification matrix mark provisional results.
- The **assumptions register**, a deliverable, is the deduplicated set of reasons, each with the node that introduced it.

### 1.6 Sourced values (requirement lineage)
- Values bound from requirements are `Sourced(value, sources=[requirement IDs], provisional=[reasons])`.
- A `design_inputs` node aggregates them: one field per design driver, e.g. `f_max_Hz`, `envelope_max_mm`,
  `random_asd`, `qsl_g`.
- Consumers reference fields by path. The graph can then answer:
  - which requirement acts on which node
  - which downstream results a requirement change invalidates

  This is lineage tracing: forward along edges that carry the affected field.
- If no requirement provides a value, use a declared **fallback**. Its use is always provisional and always reported.

### 1.7 Mutation, loops and controllers
- `g.set(node, **bindings)` changes bindings, e.g. after a user click, a UI drag or a sizing lever. Re-evaluation is
  incremental because of the cache.
- Iteration (sizing, trade studies) is a **controller outside the DAG**: evaluate → read verification rows → change
  bindings → re-evaluate.
- Controllers must:
  - act only through bindings
  - log every action with its reason
  - **trial-evaluate** each change and keep it only if the targeted margin improves by a threshold; otherwise revert
    and log "tried, reverted"
  - apply a lever only to gaps it can plausibly close (a declared reach). Beyond that, emit advice, not more changes.
    Unbounded levers produced absurd designs in the prototype (a lid with 47 screws for 3 dB).
- Carry sized bindings explicitly when a controller rebuilds the graph for a costlier phase. Use a declared list of
  `(node, binding names)`, not ad-hoc copying.

### 1.8 Fidelity tiers and decision nodes
- Model the same physics at several fidelities as **separate nodes**, e.g. rule-of-thumb → semi-analytical →
  full-wave/FE.
- A **decision node** consumes the cheap tier plus the requirement. Per item it outputs:
  - a verdict: PASS / FAIL / NEEDS-HIGHER-TIER / OPEN
  - the numbers it used and a reason string
- The verdict must be reproducible from the logged numbers alone (provide `replay(record)`).
- Expensive nodes are added to the graph only when enabled (opt-in flag). Their requests come from the decision node,
  never from "run everything".
- Every tier declares its **validity**: frequency or size ranges, uncertainty U. Outside validity there is no credit:
  the item goes up a tier or stays OPEN.
- A higher-tier result must state whether it is **resolved**, e.g. a time-domain record that has decayed enough or a
  converged solve. An unresolved result yields OPEN, never a verdict.
- Default to reliable baseline settings. Refinement and convergence studies are opt-in: safety factors cover moderate
  model error.

### 1.9 Verification as nodes
- Verification nodes emit `Row(req_ids, topic, requirement, method[A/R/I/T], evidence(node + path), value, margin,
  verdict, provisional)`.
- `verdict` is one of PASS / FAIL / OPEN / DECIDE / DELIVERABLE.
- Margins are numbers with sign conventions (> 0 is good).
- Contradictory requirements produce DECIDE rows, never a silent pick.

### 1.10 Serialisation and views
- `graph.json`: nodes, bindings (refs with paths, constants, provisional reasons), types, versions, sources, provenance,
  and value previews.
- The graph view is a first-class deliverable (§4).

---

## 2 How to connect things: patterns and rules

The quality of the graph is decided by its wiring. Follow these rules.

1. **One concern per node.** A node computes one engineering decision or result (e.g. `lid_seam`, `venting`,
   `mounting_feet`).
   - Split when two outputs have different consumers or a different provisional status.
   - Merge when outputs always change together.
2. **Fan-in through `design_inputs`.**
   - Requirements never connect directly to rules. They bind into `design_inputs` (typed, sourced fields); rules take
     paths into it.
   - Then a requirement change hits exactly the fields it sources, and lineage stays exact.
3. **Reference by path, not by whole object, when only a field is used.**
   - Use `Ref("design_inputs", "f_max_Hz.value")`, not `Ref("design_inputs")`.
   - This documents the dependency, narrows invalidation and makes the edge label meaningful in the graph view.
4. **Stages in a fixed order.** inputs → requirements → design inputs → layout & rules → draft (2-D) → geometry (solids)
   → analysis (tiered) → decision → verification.
   - Every node has a stage. Edges go forward. An edge that seems to need to go backwards is a controller (§1.7), not
     an edge.
5. **Geometry as data first.**
   - Rules emit draft entities: points, curves, regions with boolean, offset and fillet recipes.
   - Solids are built from regions in one place (`solidify`). Downstream analyses consume rule outputs or draft
     entities, never re-derive geometry ad hoc.
   - Keep B-rep shapes out of cache keys; hash their recipes.
6. **Design variables live in one parameter node** (e.g. `layout_params`), overridable from the input file.
   Controllers change only that node or explicitly listed bindings.
7. **Library data is a node input with provenance:**
   - each entry has `source` and `reviewed_by`
   - unreviewed data makes consumers provisional
8. **Side effects only at the edges.** Exports (STEP, drawings, reports, solver files) happen in output functions or
   in solver nodes with artefact hashing (§1.3), never inside rules.
9. **Frames and units are declared once:**
   - Units: SI or mm-N-t-s, consistently.
   - Coordinate frames: one housing frame with a stated origin and axes.
   - Every node that crosses frames does the conversion explicitly and is tested.
10. **Assumptions are visible.**
    - Every placeholder number is a `Provisional` or carries a `[P]` mark in its docstring and in the reason string.
    - Never hard-code a guess without a reason string.
11. **Naming:** nodes are nouns for results (`seam`, `vents`, `layout`) and the functions are verbs or rule names. Edge
    labels in the view are `param ← node.path`.

**Anti-patterns to avoid:**
- a god node that returns a dict of everything
- rules that read files or globals
- verification that recomputes physics instead of reading analysis nodes
- controllers that bypass `g.set`
- "magic" fallbacks that are not provisional

---

## 3 Reference vertical slice (to prove the core)

Implement the core first (§1), then this slice on synthetic data. Keep domain logic in plugins (entry points or a
`domains/` package) so the core has no enclosure knowledge.

1. **Inputs:**
   - a synthetic PCB: outline, mounting holes, components with heights, connector headers
   - read from a simple YAML or from **KiCad** (`.kicad_pcb`) and IDF 3.0
   - a connector list: part, contacts, destination, EMC class
   - a variant file (YAML, with a JSON Schema)
2. **Requirements:**
   - typed **cards**: id, kind from a closed catalogue, params, verbatim quote, method, tbc, status
   - loaded from YAML/JSON or a **ReqIF** file
   - a fidelity checker: numbers appear in the quotes, units parse, kinds are known
   - binding rules: the strictest threshold wins, load cases all apply, contradictions become DECIDE
   - card writing may be manual or LLM-assisted. The LLM is optional and never in the evaluation path.
3. **Rules:**
   - envelope / layout, with the board placed and gaps from the wire bend radius
   - connector wall assignment: destination → wall, then a search over walls per group with cost = footprint + wire
     length + route penalty, class-ordered rows
   - lid seam pitch / gasket decision
   - vents (waveguide below cutoff)
   - mounting feet, finish selection, 2-D DFM (tool radii, webs)
4. **Draft → solid** with CadQuery; STEP export; mass properties.
5. **Analyses (tiered):**
   - **Structural:** plate-frequency rules → CalculiX modal / random / quasi-static on a defeatured model, with mode
     ownership by RMS displacement groups
   - **Thermal:** lumped network
   - **EMI:** rule tier → semi-analytical aperture model (cite the published method you implement, validate against its
     published curves) → optional reduced-band openEMS. A decision node routes between the tiers (§1.8).
6. **Verification matrix + sizing controller** (§1.7, §1.9).
7. **Outputs:** report (HTML → PDF via headless Chromium), a viewer (§4), and a review data package generator with a
   neutral template.

---

## 4 The graph view (required)

A single-file HTML viewer, no server needed. It is generated from `graph.json` and opens in a browser.

- **Layout:**
  - stage-coloured columns, left to right
  - a layered layout (longest path, with each stage starting after the previous one)
  - barycentric sweeps to reduce edge crossings
- **Nodes** in Grasshopper style:
  - a header with the node name and `function vN`
  - one input port per parameter, an output port on the right
  - a dashed border when not evaluated, an orange border and "!" when provisional
- **Edges** go from the producer's output to the consumer's specific port, labelled `.field` when a path is used.
- **Interaction:**
  - Click a node to highlight its full upstream and downstream closure (dim the rest).
  - The click also opens a side panel: function, version, source, inputs (linked), constants, consumers (with fields),
    provisional reasons, requirement lineage and a value preview.
  - Requirement-lineage mode: pick a requirement ID and highlight the fields it sets and every node reached via edges
    that carry those fields.
  - Search, zoom and pan; a 1:1 / overview toggle.
- Plain ES2017 JavaScript, no build step. Test it headless (Playwright): no console errors, screenshots of the main
  states.

---

## 5 Milestones (each testable before the next)

| # | Deliverable | Done when |
|---|---|---|
| M0 | Scaffold: package, CLI `dg doctor`, CI (lint, types, fast tests), licence/SPDX checker, customer-ID/secret guard | CI green on an empty core |
| M1 | Graph core: nodes, Refs with paths, type checks, topo evaluation, cache (mem + disk), provenance, provisional propagation, `set`, serialisation | Unit tests cover every rule in §1.1–1.6, including path type errors, cache hits after `set`, and propagation through lists |
| M2 | Sourced values + requirement cards + binding + lineage queries | Changing one card invalidates exactly the expected nodes (test), and the lineage query returns them |
| M3 | Graph viewer (§4) | Headless test: no console errors; a lineage highlight and a node selection snapshot |
| M4 | Draft layer + solidify + STEP | A synthetic enclosure exports a valid solid; the 2-D DFM checks pass/fail on crafted cases |
| M5 | Rules slice + verification matrix | `dg run examples/board_a.yaml --no-solvers` produces a matrix with sourced rows and provisional marks |
| M6 | Controller (sizing) with trial evaluation, lever reach, logged actions | A test where a lever would worsen the margin is reverted, and one beyond its reach yields advice only |
| M7 | Solver nodes (CalculiX, openEMS) with artefact hashing, process-tree timeouts, resolved/unresolved flags | Second run reuses the solve (no solver process started); timeout test kills a grandchild process |
| M8 | Tiered decision node + replay | Decision table tests; `replay(log)` reproduces every verdict |
| M9 | Review package (reports, data package, neutral template) + docs + tutorial + benchmark suite | A new user follows the tutorial in under 15 minutes; the benchmarks reproduce published reference results within stated tolerances |

---

## 6 Lessons from the prototype (do not repeat these mistakes)

- **Timeouts:** `subprocess.run(timeout=…)` killed only the direct child and then waited for the pipes; a 30-min
  timeout became 12 h. Kill the whole process tree (Windows: `taskkill /T /F`), then raise.
- **Re-solving:** hash solver decks and reuse results (§1.3).
- **Unresolved high-fidelity results:**
  - A nearly undecayed time-domain record with a "perfect" tail fit was accepted as reliable and produced a nonsense
    FAIL.
  - Require a minimum decay before trusting extrapolation; otherwise the verdict is OPEN.
- **FE coupling pitfalls:**
  - Tie surfaces that get split by boolean operations leave most slave nodes untied: check and report tie coverage.
  - Shell nodes cannot take beam or rigid multi-point constraints in CalculiX: use equation-type couplings.
  - Curved quadratic tets on thin walls produced bad Jacobians and solver hangs: use straight-sided quadratic tets by
    default.
- **Model size:** thin walls plus many small features exploded a solid mesh to 4× the elements, and the solve took
  12 h with the default direct solver. Defeature analysis models deliberately, keep feature counts in check, and expose
  a faster solver option.
- **PDF generation:** a Python HTML-layout engine took more than 40 minutes for a 500-row table; headless Chromium
  took 4 s.
- **Binding semantics:** "first card wins" was wrong. Strictest threshold wins, load cases all apply, contradictions
  are surfaced.
- **Tests and exit codes:** never pipe test output through `tail` before reading the exit code.
- **Lossless-cavity models:** improving an aperture can sharpen a resonance just above the credited band and *reduce*
  the computed margin. This is why levers are trial-evaluated (§1.7).

---

## 7 Out of scope / never include

- Any real company's requirement documents, formats, IDs, logos, design language, product variants or classification
  markings.
- Library values copied from copyrighted catalogues without a public source. Prefer public specifications and cite them.
- An LLM call inside graph evaluation. The graph must evaluate deterministically offline.

## 8 Final report expected from you

- What exists, per milestone, with the test results.
- The validation benchmarks and their tolerances.
- Open issues.
- A short guide for adding a new domain: new nodes, a new stage, a new requirement kind.
