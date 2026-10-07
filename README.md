# Design Intent Graph

An open-source Python library and CLI for typed, provenance-aware engineering design graphs.
The current implementation covers the M0-M2 foundation: typed references, validation, topological evaluation,
deterministic memory/disk caching, provisional propagation, provenance, mutation, JSON export, synthetic
requirement cards, fidelity checks, strict-threshold binding, contradiction reporting, and field-sensitive
requirement lineage.

## Requirements

Python 3.12 or newer.

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
dg doctor
pytest
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
graph.add("load", lambda: Provisional(Load(value=4.0), "example input"), {})
graph.add("result", load_result, {"load": Ref("load")})
graph.check()
result = graph.evaluate("result")["result"]
graph.to_json("graph.json")
```

The enclosure workflow, graph viewer, CAD, analyses, controllers, solver integrations, and review package remain
planned milestones. The graph core is intentionally domain-neutral.

## Licensing and data hygiene

The project is Apache-2.0. `python scripts/check_repo.py` checks source SPDX headers and scans tracked project
content for common credential patterns and labeled customer/account identifiers. Only synthetic examples and
fixtures belong in this repository. Solver tools with GPL licenses must remain separate executables or optional
extras; do not add a GPL dependency to the core package.