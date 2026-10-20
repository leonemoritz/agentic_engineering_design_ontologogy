# Design Intent Graph

An open-source Python library and CLI for typed, provenance-aware engineering design graphs. The implementation
includes the graph core, typed synthetic requirements, a standalone graph viewer, recipe-first 2-D enclosure
geometry and DFM checks, a synthetic enclosure run, bounded sizing, replayable decisions, external solver process
management, and report/review-package generation. Requirement cards load from JSON/YAML or a documented ReqIF
mapping; project YAML supports external requirement and layout-variant files.

## Requirements

Python 3.12 or newer.

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m playwright install chromium
dg doctor
pytest
dg run examples/board_a.yaml --no-solvers --output-dir build/board_a
```

The example writes `graph.json`, `verification.json`, `graph.html`, `report.html`, `review_template.yaml`, and
`review_package.zip`. To view an existing serialized graph, run `dg view graph.json`; it writes `graph.html` next
to the input file. The HTML viewer is offline and server-free.

Check an external requirement set with `dg requirements tests/fixtures/synthetic_requirements.reqif`. Run the
deterministic analytical microbenchmark with `PYTHONPATH=src python scripts/benchmark_analytical.py`. Add `--step`
to `dg run` to export the drafted base as STEP and write its mass properties; this needs the `cad` extra. Add
`--pdf` to request a PDF report through Playwright Chromium.

## System model and requirement assessment

Requirements live in an MBSE-style model (`design_graph.model`): needs, parts, requirements, verification cases,
assumptions, evidence, and typed relationships (`derive`, `refine`, `satisfy`, `verify`, `allocate`, `trace`).
Status is derived, never asserted:

```sh
dg model validate examples/model_enclosure.yaml   # structure, traceability, dimensions, authority
dg model assess examples/model_enclosure.yaml     # SATISFIED / NOT_SATISFIED / OPEN / BLOCKED per requirement
dg model assess --strict examples/model_enclosure.yaml   # exit 1 unless everything is satisfied
dg model matrix examples/model_enclosure.yaml    # requirement x verification-case matrix
dg model import-cards cards.json --subject SYS -o model.yaml   # cards become unapproved requirements
```

A requirement is `SATISFIED` only when a human approved it (pinned to its content hash), a part claims it, every
`verify` case is approved and has current evidence that passes, no assumption is open, it is not TBC, and all derived
requirements are satisfied. Pass/fail and margin are computed from the measured value, unit, and uncertainty; evidence
goes stale when the requirement, case, subject part, or declared tool changes. Agents cannot approve or attest. The
toolchain is declared by the engineer (`ToolDeclaration`); its credibility is not assessed. See
`docs/design/universal-design-graph.md`.

## Process layer and agent roles

The system model says whether a design is verified. The process layer (`design_graph.process`) says
what is missing, which stage it belongs to, what to do, and who should do it:

```sh
dg process status examples/model_process_bracket.yaml      # stage states and next actions
dg process open-items examples/model_process_bracket.yaml --all
dg process plan examples/model_process_bracket.yaml        # tasks per agent role, with briefs
dg process roles                                           # roles, write permissions, checklists
```

- **Engineering elements** (`design_graph.model.engineering`): Statement, DesignRule, Concept,
  TradeStudy (decision checked against its scores), LoadCase, FailureMode, OptimizationSpec,
  Allowable, Credibility, Review and ConsistencyCheck. They are optional; existing models, hashes
  and approvals are unchanged.
- **Stage template** (`DEFAULT_TEMPLATE`, or YAML via `--template`): requirements, concepts,
  allowables, sizing, analysis, verification, test, each with dependencies, required content and
  an exit criterion.
- **Completeness and consistency rules** turn missing work into open items. Examples: a statement
  that isn't traced; no trade study; a load-case requirement without failure modes; a tier-1 result
  without a higher-tier comparison; a mesh that hasn't converged; an unreviewed trade study;
  disagreeing models (for example CAD mass against sizing mass), which become a rework item at the
  named feedback stage.
- **Agents** (`design_graph.agents`): a manager turns open items into tasks for expert roles. The
  orchestrator applies a proposal only if the role may write those collections and it adds no
  validation error. An independent quality role then reviews it, seeing the changed elements but
  not the author's reasoning. Agents can never write approvals, baselines, principals or tool
  declarations, and a review is never an approval. Backends: `CallableBackend` (scripted) and
  `PromptBackend` (any LLM through a `complete(system, user)` function; no SDK dependency).

```python
from design_graph.agents import Orchestrator, PromptBackend
report = Orchestrator(model, PromptBackend(my_complete)).run()
report.status.stages, report.records, report.human_actions
```

## Formal ontology (OWL 2 and SHACL)

The system model is published as a formal ontology in `ontology/`:

- `design-graph.ttl`: OWL 2 vocabulary: 22 element classes under `dg:Element`, 9 nested
  value classes such as `dg:Quantity` (aligned to `qudt:QuantityValue`), object and datatype properties
  with domains and ranges, and the relationship properties `dg:derive`, `dg:refine`,
  `dg:satisfy`, `dg:verify` and `dg:allocate` as subproperties of `dg:trace`.
- `design-graph-shapes.ttl`: SHACL Core shapes for required fields, cardinalities, datatypes,
  enumerations, id patterns, reference targets, relationship endpoints, human-only approvals and
  tool declarations, and reviewer independence.

Both files are generated from the Pydantic classes, and a test fails if they are out of date. The
shapes are never stricter than `dg model validate`, so a model that validates also conforms. Each
broken model in the tests is rejected by both. Rules that SHACL Core cannot express (derived
status, hash pinning, units, cycles, ...) stay in Python and are listed in the ontology header.

```sh
python -m pip install -e '.[ontology]'
dg ontology export -o ontology                     # regenerate vocabulary and shapes
dg model export-rdf examples/model_enclosure.yaml -o enclosure.ttl --check   # data + SHACL
dg model export-rdf examples/model_enclosure.yaml -o enclosure.jsonld --format jsonld
```

Elements become individuals `<base><id>` (default base `urn:design-graph:element:`; set it with
`--base`). Relationships are exported both as `dg:Relationship` individuals and as direct triples
such as `el:ENCL dg:satisfy el:R-MASS`. The namespace `https://w3id.org/design-graph/` is not yet
registered with w3id.org.

## MCP server

`design-graph-mcp` exposes the system model and process layer to agents over MCP (stdio). It needs
the `mcp` extra. Tool paths are relative to `--root`; nothing outside it is read.

```sh
python -m pip install -e '.[mcp]'
claude mcp add design-graph -- design-graph-mcp --root "$PWD"
```

Tools: `validate_model`, `get_verification_status`, `get_verification_matrix`,
`get_process_status`, `list_open_items`, `plan_tasks`, `list_roles`, `get_element`,
`propose_change`. All are read-only and return structured content. `propose_change` runs a role's
proposal through the orchestrator's permission and validation gate and reports the items it would
resolve and raise, without writing the model. Approvals, attestations, baselines, principals and
tool declarations are not reachable. Errors are tool errors carrying JSON with a `code`. A tour of
the repository is in `docs/walkthrough.md`.

## Layout

```
src/design_graph/
  core/          domain-neutral graph engine (rules, provenance, caching)
  model/         MBSE model, engineering elements, validation, assessment, baselines, evidence bridge
  process/       stage templates, completeness and consistency rules, stage status, next actions
  agents/        roles, orchestrator, callable and prompt backends
  mcp/           MCP server over the model and process layer (read and dry-run only)
  engineering/   analysis rules, geometry/CAD, connectors, solver runner, decision records, controller
  interchange/   requirement import (JSON/YAML/ReqIF), OWL/SHACL/RDF export, viewer, report
  domains/       example domain plug-ins (enclosure)
docs/            walkthrough; design/ holds the design document
ontology/        generated OWL vocabulary and SHACL shapes (Turtle)
examples/        synthetic example inputs
tests/           pytest suite (tests/model for the system model)
```

## Minimal graph

```python
from pydantic import BaseModel
from design_graph import Graph, Provisional, Ref, rule


class Load(BaseModel):
    value: float


class Result(BaseModel):
    doubled: float


@rule(version="1", source="synthetic example")
def load_result(load: Load) -> Result:
    return Result(doubled=2 * load.value)


graph = Graph()


@rule(version="1", source="synthetic example input")
def load_input(value: Load) -> Load:
    return value


graph.add("load", load_input, {"value": Provisional(Load(value=4.0), "example input")})
graph.add("result", load_result, {"load": Ref("load")})
graph.check()
result = graph.evaluate("result")["result"]
graph.to_json("graph.json")
```

## Optional Tools

CadQuery solidification and STEP export are available through the `cad` extra:

```sh
python -m pip install -e '.[cad]'
```

PDF output requires the `pdf` extra and Playwright Chromium:

```sh
python -m pip install -e '.[pdf]'
playwright install chromium
```

Solver execution is delegated to separate executables. The generic runner hashes solver decks, reuses matching
successful runs, and terminates the POSIX process group on timeout; no GPL solver is linked or installed by this
package.

## Scope

`examples/board_a.yaml` is invented synthetic data. The current enclosure plug-in covers rectangular board
placement, mounting-hole/base recipes, tool-radius and web checks, connector wall assignment, a declared seam pitch,
a rectangular-waveguide cutoff estimate, and sourced verification rows. The library also includes a safety-factored
thin-plate estimate and a two-node steady-state thermal network. These analytical models are not solver-grade
qualification evidence. The CAD path is optional and has not been exercised in the base CI environment.

The remaining brief items are not implemented yet: KiCad/IDF readers, full enclosure design rules (including gasket,
feet, finish, connector grouping refinements, and drawings), FEM/modal/random/quasi-static and full thermal/EMI
tiers, CalculiX/openEMS adapters and their physics-validation fixtures, automatic high-fidelity request orchestration,
mode ownership, convergence studies, LLM card-writing support, and a published-reference benchmark suite. The
generic controller, decision replay, external-process wrapper, analytical examples, HTML report, and review archive
are foundations for those integrations, not substitutes for them. Local CI skips Playwright browser automation when
the optional package is absent and skips solid/STEP execution when CadQuery is absent; CI installs Chromium, while
the CAD test remains opt-in through the `cad` extra.

## Licensing and data hygiene

The project is Apache-2.0. `python scripts/check_repo.py` checks source SPDX headers and scans tracked project
content for common credential patterns and labeled customer/account identifiers. Only synthetic examples and
fixtures belong in this repository. Solver tools with GPL licenses must remain separate executables or optional
extras; do not add a GPL dependency to the core package.