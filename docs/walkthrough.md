# Walkthrough

A 10-minute tour of the repository: what each layer does, in the order you meet it, with one
command per step. Run everything from the repo root after `python -m pip install -e .`; only the
ontology and MCP steps need an extra.

## 1. The idea in one paragraph

An engineering design is held in a **system model** (YAML): needs, parts, requirements,
verification cases, evidence, approvals and engineering elements such as load cases and trade
studies. Nothing in the file says "this requirement is satisfied". Status is **derived** from the
model by rules: approvals pinned to content hashes, evidence whose pass/fail and margin are
computed from measured values with units, and staleness when anything upstream changes. A
**process layer** turns what is missing into open items with an owner and an action, and
**agents** can work those items under permission, validation and independent review. People keep
the approvals, attestations and tool declarations.

## 2. Map of the code

| Package | Role | Start reading at |
|---|---|---|
| `core/` | Domain-neutral typed DAG: `@rule`, `Ref`, `Provisional`, provenance, cache | `graph.py` (`Graph.add`, `evaluate`) |
| `model/` | MBSE elements, YAML store, validation, derived assessment, baselines | `system.py`, then `assess.py` |
| `process/` | Stage template, completeness/consistency rules (PR-*), stage status | `status.py` (`evaluate_process`) |
| `agents/` | Roles with write permissions, orchestrator, callable and prompt backends | `orchestrator.py` (`plan`, `apply`, `run`) |
| `mcp/` | MCP server over the model and process layer (read and dry-run only) | `tools.py` |
| `engineering/` | External solver runner, trial sizing controller | `solver.py` |
| `interchange/` | Requirement import (JSON/YAML/ReqIF), OWL/SHACL/RDF export, graph viewer | `ontology.py`, `requirement_io.py` |

Layers depend downward only: `agents` → `process` → `model`; `core` knows nothing about any of them.

## 3. Step by step

**Is the model well formed?** Validation finds structural and traceability errors (dangling
references, dimension mismatches, agents holding human-only roles).

```sh
dg model validate examples/model_system.yaml
```

**What is verified?** Each requirement gets a verification status (`VERIFIED`, `FAILED`, `STALE`,
...) and a satisfaction status (`SATISFIED`, `OPEN`, `BLOCKED`, ...) with machine-readable reasons.

```sh
dg model assess examples/model_system.yaml
dg model matrix examples/model_system.yaml
```

**What should happen next?** The process layer evaluates each stage (requirements → concepts →
allowables → sizing → analysis → verification → test) and lists the work in order.

```sh
dg process status examples/model_process_bracket.yaml
dg process open-items examples/model_process_bracket.yaml --all
```

**Who does it?** The orchestrator groups actionable items into one task per role, each with a brief
built from the role's checklist. `dg process roles` shows what each role may write.

```sh
dg process plan examples/model_process_bracket.yaml
```

In Python, `Orchestrator(model, backend).run()` executes those tasks: each proposal is checked for
permissions and new validation errors, then reviewed by the quality role, which sees the changed
elements but not the author's reasoning. See `tests/process/test_orchestrator.py`.

**As a formal ontology.** The model's classes are published as OWL 2 with SHACL shapes in
`ontology/`. Any model exports as RDF data and can be checked against the shapes with standard
tools (pySHACL, TopBraid, Protégé, a SPARQL store).

```sh
dg model export-rdf examples/model_system.yaml -o build/system.ttl --check
```

**The graph core** is independent of all of the above: typed rules with provenance and input
hashes. `evidence_from_graph` (`model/baseline.py`) turns an evaluated node into model evidence,
bound to the current basis hash. A serialized graph opens in an offline viewer:

```sh
dg view graph.json
```

## 4. Driving it from an agent (MCP)

`design-graph-mcp` exposes the model and process layer as MCP tools over stdio. Install the extra
and register it with your client; paths passed to tools are relative to `--root`, and nothing
outside it is read.

```sh
python -m pip install -e '.[mcp]'
claude mcp add design-graph -- design-graph-mcp --root "$PWD"
```

| Tool | Answers |
|---|---|
| `validate_model` | Is the file well formed? |
| `get_verification_status`, `get_verification_matrix` | What is verified and satisfied, with margins? |
| `get_process_status`, `list_open_items`, `plan_tasks` | What is the next work, for which role? |
| `list_roles`, `get_element` | What may a role write; what does an item point at? |
| `propose_change` | Would this change pass the gate, and what would it resolve or raise? |

Every tool is read-only. `propose_change` runs the orchestrator's gate as a dry run and never
writes the model: accepted changes still go through review and a person. Failures come back as tool
errors whose text is JSON with a `code` (`PATH_OUTSIDE_ROOT`, `MODEL_INVALID`, `UNKNOWN_ROLE`, ...).

## 5. Where to go next

- `ontology/design-graph.ttl`: the vocabulary, readable in any OWL editor.
- `conformance/verification-semantics.md`: the hashing and assessment rules, with golden vectors.
- `tests/model/helpers.py` and `tests/process/proc_helpers.py`: the smallest complete models.
