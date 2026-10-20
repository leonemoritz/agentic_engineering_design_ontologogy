# Agentic Engineering Design Ontology

This repository is designed as a general entry point for agentic AI in engineering. It gives
agents and engineers one shared, typed system model to work on: requirements, design, evidence
and approvals. Status is computed from that model rather than asserted by anyone. Authority
boundaries are explicit: agents can propose changes and produce evidence, but only humans can
approve requirements or attest results. The core is domain-neutral. Agents connect through MCP or
through any LLM, using a plain `complete(system, user)` function.

For an end-to-end example, see
[Engineering Ontology for Agentic Design](https://www.moritzvalentino.de/blog/engineering-ontology-for-agentic-design).
An agent takes a short brief for a 3D-printed bridge through requirements, concept selection,
allowables, sizing, CAD and FEM. The approvals and the physical test are left to a person.

## Scope

The project starts high level: the system model, the engineering process, and the authority
rules that decide when a design counts as verified. Tools sit behind that boundary. Ten
open-source CAx tools connect through thin [connectors](#cax-connectors) that run each tool as a
separate executable and turn one named output into evidence. The connectors do not build models
for you: you write the deck, script or case, and the connector stages, runs and reads it. There is
no loader for declarative graph specs yet, and commercial tools are not connected; how the
ontology would bind to 3DEXPERIENCE and Rhino/Grasshopper is designed in
[docs/binding-3dexperience-grasshopper.md](docs/binding-3dexperience-grasshopper.md).

## Quick start

Python 3.12 or newer. The system model, process layer and agents need only the base install
(pydantic, click, PyYAML, pint): no browser, CAD kernel or solver.

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
dg model assess examples/model_system.yaml
dg process status examples/model_process_bracket.yaml
```

Two optional extras add features: `mcp` (MCP server) and `ontology` (RDF export and SHACL
checks). For development, see [Development](#development).

## System model and requirement assessment

Requirements live in an MBSE-style model (`design_graph.model`): needs, parts, requirements, verification cases,
assumptions, evidence, and typed relationships (`derive`, `refine`, `satisfy`, `verify`, `allocate`, `trace`).
Status is derived, never asserted:

```sh
dg model validate examples/model_system.yaml   # structure, traceability, dimensions, authority
dg model assess examples/model_system.yaml     # SATISFIED / NOT_SATISFIED / OPEN / BLOCKED per requirement
dg model assess --strict examples/model_system.yaml   # exit 1 unless everything is satisfied
dg model matrix examples/model_system.yaml    # requirement x verification-case matrix
dg requirements cards.json                       # check that each card's numbers appear in its quote
dg model import-cards cards.json --subject SYS -o model.yaml   # cards become unapproved requirements
```

Requirement cards are the import format (JSON, YAML or ReqIF). A card is a quote plus, for a
quantitative requirement, `attribute`, `relation` (`le`, `ge`, `eq`, `range`), `value`, `unit`
and, where needed, `upper` or `tolerance`. Imported requirements start unapproved.

A requirement is `SATISFIED` only when a human approved it (pinned to its content hash), a part claims it, every
`verify` case is approved and has current evidence that passes, no assumption is open, it is not TBC, and all derived
requirements are satisfied. Pass/fail and margin are computed from the measured value, unit, and uncertainty; evidence
goes stale when the requirement, case, subject part, or declared tool changes. Agents cannot approve or attest. The
toolchain is declared by the engineer (`ToolDeclaration`); its credibility is not assessed. The
exact rules are in `conformance/verification-semantics.md`.

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

## Computation graph and evidence

Analysis values come from a typed computation graph (`design_graph.core`). Each node is a
versioned rule with recorded provenance and input hashes. `evidence_from_graph`
(`design_graph.model.baseline`) evaluates a node and turns the result into model evidence, bound
to the current basis hash and carrying the node's provenance. That is how a computed result
becomes something the assessment can count. `dg view graph.json` renders a serialized graph as an
offline HTML viewer.

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

External solvers run as separate executables through `run_solver`, which hashes the input deck,
reuses matching successful runs, and terminates the process group on timeout. `SizingController`
trials one declared binding change at a time and keeps it only when the margin improves.

## CAx connectors

`design_graph.connectors` runs open-source CAx tools as separate processes, so no GPL code is
linked into the package. A connector copies your input deck (a file or a case directory) into a
work directory and fills its placeholders from parameters. It then runs the tool through
`run_solver` and reads the tool's own output files into named quantities. A run is reused while
the rendered inputs, parameters and command are unchanged.

> **Mostly untested against the real tools.** FreeCAD is the exception: it has been used end to
> end through the FreeCAD MCP in the [linked example](https://www.moritzvalentino.de/blog/engineering-ontology-for-agentic-design).
> The other connectors have not yet been run against real installations. The test suite drives them
> with fake executables that write each tool's output format. Their command lines (flags such as
> `kicad-cli pcb drc --format json`, `gmsh -format msh41`, `ccx -v`) follow each tool's documented
> usage but are unverified. Run one known case per tool and check the outputs before you rely on a
> connector for evidence. Reports and fixes are welcome.

| Connector | Tool | Kind | You provide | Outputs |
|---|---|---|---|---|
| `opencascade` | Open CASCADE (OCP, CadQuery, pythonocc) | CAD kernel | Python script, run by an interpreter with the bindings | what the script writes to `results.json` |
| `freecad` | FreeCAD (`freecadcmd`) | CAD | Python script | `results.json` |
| `openscad` | OpenSCAD | CAD | `.scad` model | volume, area and extents of the exported STL |
| `gmsh` | Gmsh | Meshing | `.geo` script | node and element counts |
| `calculix` | CalculiX (`ccx`) | FEA | `.inp` deck with `*NODE PRINT` / `*EL PRINT` | max displacement, max von Mises stress, reaction force totals per set |
| `code_aster` | code_aster (`run_aster`) | FEA | `.export` + `.comm` (support files) | `results.json` written by the `.comm` file |
| `elmer` | Elmer FEM (`ElmerSolver`) | Multiphysics | `.sif` with SaveScalars | last row of the scalars file |
| `openfoam` | OpenFOAM | CFD | case directory (`Allrun` or a solver) | last row of each function object under `postProcessing/` |
| `su2` | SU2 (`SU2_CFD`) | CFD | `.cfg` writing a CSV history | last row of `history.csv` |
| `kicad` | KiCad (`kicad-cli`) | ECAD | `.kicad_pcb` board | DRC violation, error, warning and unconnected counts |

A placeholder is `{{name}}` for a dimensionless parameter or `{{name:unit}}`, which writes the
parameter converted to that unit. Every placeholder needs a parameter, and every parameter must be
used. `$` macros in OpenFOAM dictionaries are left alone. Units of outputs are never guessed. Most
tools do not record units, so you declare them (`-u length=mm -u stress=MPa`, or per output name).
An output with no unit can be printed but not recorded as evidence. Each tool is found through
`--exe`, then `$DG_<ID>_EXE`, then `PATH`.

```sh
dg connectors list                                   # which tools are installed, and their versions
dg connectors run calculix job.inp -w runs/c1 \
    --params-from model.yaml:SYS --attr x -u length=mm -u stress=MPa
dg connectors run calculix job.inp -w runs/c1 --params-from model.yaml:SYS --attr x \
    -u length=mm -u stress=MPa --record model.yaml --output max_von_mises.EALL \
    --case C1 --requirement R1 --by A1 --evidence-id E-C1-1
```

`--record` appends the output to the model as evidence. The evidence is bound to the current basis
of the requirement and case. Its provenance is `<connector>:<output>` with the run's input hash
and tool version. Recording is refused when the run's tool version differs from the version in the
case's `ToolDeclaration`, or when the new evidence would make the model invalid. In Python:
`get_connector(...).run(...)`, `parameters_from_part`, `evidence_from_run` and `declare_tool`.

## Requirements engineering templates

`templates/` holds two starting points:

- `requirement-cards.yaml`: a fill-in card file. It has one card for each relation (`le`, `ge`,
  `range`, `eq`), a card with a unit, a TBC card and a qualitative card, with the field rules in
  comments.
- `requirements-engineering.yaml`: a process template with three stages: elicitation and
  specification, verification planning and execution (tools and connector evidence), and review
  and sign-off. It reuses the stage ids of the process rules, so the built-in rules report into it.

```sh
dg requirements templates/requirement-cards.yaml
dg model import-cards templates/requirement-cards.yaml --subject SYS -o model.yaml
dg process status model.yaml --template templates/requirements-engineering.yaml
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
dg model export-rdf examples/model_system.yaml -o system.ttl --check   # data + SHACL
dg model export-rdf examples/model_system.yaml -o system.jsonld --format jsonld
```

Elements become individuals `<base><id>` (default base `urn:design-graph:element:`; set it with
`--base`). Relationships are exported both as `dg:Relationship` individuals and as direct triples
such as `el:SYS dg:satisfy el:R1`. The namespace `https://w3id.org/design-graph/` is not yet
registered with w3id.org.

The ontology carries structure, not verdicts. Hashing, invalidation and requirement status are
specified separately in `conformance/verification-semantics.md`, with golden vectors and an
independent standard-library checker that reproduces them:

```sh
python -I conformance/independent/check.py conformance
```

## Development

```sh
python -m pip install -e '.[dev]'
dg doctor
pytest
```

Chromium is optional for development as well: the graph viewer's browser test skips when it is
missing. CI installs it with `python -m playwright install chromium`.

## Layout

```
src/design_graph/
  model/         MBSE model, engineering elements, validation, assessment, baselines, evidence bridge
  process/       stage templates, completeness and consistency rules, stage status, next actions
  agents/        roles, orchestrator, callable and prompt backends
  mcp/           MCP server over the model and process layer (read and dry-run only)
  core/          domain-neutral computation graph (rules, provenance, caching)
  interchange/   requirement import (JSON/YAML/ReqIF), OWL/SHACL/RDF export, graph viewer
  engineering/   external solver runner, trial sizing controller
  connectors/    connectors to ten open-source CAx tools, output readers, evidence bridge
docs/            walkthrough; binding design for 3DEXPERIENCE and Rhino/Grasshopper
templates/       requirement-card template and requirements-engineering process template
ontology/        generated OWL vocabulary and SHACL shapes (Turtle)
conformance/     verification semantics spec, golden vectors, independent checker
examples/        synthetic example inputs
tests/           pytest suite (tests/model for the system model)
```

## Licensing and data hygiene

The project is Apache-2.0. `python scripts/check_repo.py` checks source SPDX headers and scans tracked project
content for common credential patterns and labeled customer/account identifiers. Only synthetic examples and
fixtures belong in this repository, and they stay domain-neutral: domain examples live outside it. Solver tools
with GPL licenses must remain separate executables; do not add a GPL dependency to the core package.
