# Binding the Design Graph Ontology to 3DEXPERIENCE and Rhino/Grasshopper

Status: design proposal, October 2026. Nothing in this document is implemented yet, except the
connector layer it builds on (`design_graph.connectors`).

## Purpose and principles

A binding maps design-graph elements to objects that already live in a host tool, so an agent can read and write the host through the graph. The graph keeps the verdicts and the authority rules; the host keeps geometry, product structure and its own lifecycle. Neither side copies the other's job.

Four rules hold for every binding:

- **Verdicts stay in the graph.** Requirement status is computed by `dg model assess` from the bound data. A host attribute such as "verified" is never read as a verdict, only as evidence or as a hint.
- **Identity is explicit.** Each bound element carries the host's stable identifier (physical ID, GUID) beside its graph id, so a sync never matches objects by name.
- **Content hashes pin across the boundary.** An approval in the graph is pinned to the content hash of what was approved. When the host object changes, the graph sees a new hash and the approval goes stale, exactly as for a YAML edit.
- **Authority does not widen.** An agent working through a host connector gets the same rights as over MCP: it may propose and produce evidence, never approve or attest. Host roles map onto graph principals; they do not override them.

The two targets sit at opposite ends. 3DEXPERIENCE is a PLM platform with its own requirements, lifecycle and approvals, so the binding is mostly about identity and authority. Rhino/Grasshopper is a parametric modeller with no PLM, so the binding is mostly about parameters flowing in and evidence flowing out.

```mermaid
flowchart TB
    agents["AI agents (MCP)"]
    graph["<b>Design graph: the source of verdicts</b><br/>Requirements, trace links, evidence, approvals,<br/>derived status, hash pinning<br/><i>Agents propose; only humans approve or attest</i>"]
    dx["<b>3DEXPERIENCE</b><br/>Engineering items, Knowledgeware parameters<br/>Requirements, route approvals<br/>SIMULIA results, event bus"]
    gh["<b>Rhino / Grasshopper</b><br/>Definitions with Get and Context components<br/>Object user text: dg:id and content hash<br/>Hops endpoint, Rhino.Compute (Windows)"]
    agents -- "proposals only" --> graph
    dx -- "REST read, ReqIF, events" --> graph
    graph -- "proposals, status" --> dx
    graph -- "bound attributes" --> gh
    gh -- "results as evidence" --> graph
```

Data from the hosts enters the graph as structure and evidence; only proposals and display status go back.

## What a binding has to map

The ontology (`ontology/design-graph.ttl`, version 0.3.0) has 22 element classes under `dg:Element`. For a binding they fall into five groups, and each group has one natural owner.

| Group | Classes | Owner after binding |
| --- | --- | --- |
| Intent | `Statement`, `Need`, `Requirement`, `Assumption`, `DesignRule` | Host PLM where one exists, else the graph |
| Structure | `Part` (with attributes), `Relationship` (`derive`, `refine`, `satisfy`, `verify`, `allocate`, `trace`) | Host product structure; trace links in the graph |
| Engineering | `Concept`, `TradeStudy`, `LoadCase`, `FailureMode`, `OptimizationSpec`, `Allowable`, `ConsistencyCheck` | Graph |
| Verification | `VerificationCase`, `Evidence`, `Credibility`, `ToolDeclaration`, `Review` | Graph; results produced in the host |
| Authority | `Principal`, `Approval`, `Baseline` (plus `Attestation`) | Graph, mirrored from host sign-offs |

Numbers are `dg:Quantity` values (aligned to `qudt:QuantityValue`) with a unit string. A binding converts host units on the way in and records the conversion; it never stores bare numbers.

Three things are deliberately not in the ontology and therefore not bound: derived status, hash computation and staleness. They are specified in `conformance/verification-semantics.md` and recomputed on the graph side after every sync.

## 3DEXPERIENCE binding

3DEXPERIENCE owns structure, requirements and lifecycle; the graph owns trace semantics, evidence and verdicts. The binding is a sync service that reads platform objects over REST, builds a graph model, assesses it, and writes back only proposals and computed status attributes.

### Object mapping

| Graph element | 3DEXPERIENCE object | Identity kept | Direction |
| --- | --- | --- | --- |
| `Part` | Engineering Item / Physical Product (VPMReference) | physical ID + revision | platform → graph |
| `Part.attributes` | Knowledgeware parameters on the reference | parameter name, unit | both: values in, proposed values out |
| `Requirement` | Requirement object (Requirements apps) | physical ID + revision | platform → graph; ReqIF as fallback |
| `Need`, `Statement` | Requirement specification / chapter, source document | physical ID | platform → graph |
| `satisfy`, `allocate` | Requirement-to-product links (coverage / implement links) | link ID | both, as proposals |
| `VerificationCase` | Test case, or a simulation process (SIMULIA) | physical ID | platform → graph |
| `Evidence` | Simulation result values, test execution result | result ID + input revision | platform → graph |
| `ToolDeclaration` | App and release that produced a result (e.g. Abaqus solver version) | app name + version | platform → graph |
| `Principal` | Platform user with roles in a collaborative space | user ID | platform → graph |
| `Approval`, `Attestation` | Route task approval / signature, maturity promotion | route task ID | platform → graph |
| `Baseline` | Released revision set / snapshot | snapshot ID | graph → platform (as a report) |
| `TradeStudy`, `FailureMode`, `LoadCase`, `ConsistencyCheck` | No native equivalent | graph id | graph only; attached as documents if needed |

### Integration surfaces

- **REST web services (3DSpace).** The primary surface: create, read, update and delete over HTTP with JSON. Calls from outside the platform authenticate through 3DPassport; each request carries a security context (role, organization, collaborative space) and a CSRF token. The service namespaces commonly used are `dseng` (engineering items), `dsreq` (requirements), `dslc` (lifecycle) and `dschg` (change actions); confirm names and versions against the tenant's developer guide.
- **Event bus (JMS).** Subscribing to change and maturity events lets the graph re-assess when an object or approval changes, instead of polling.
- **EKL / Knowledgeware.** For parameters that live in CATIA models. A small EKL action can export bound parameters with units, and a rule can read back proposed values for a designer to accept.
- **ReqIF.** The repository already imports ReqIF (`dg requirements`, `dg model import-cards`). A ReqIF export from the platform is the lowest-effort first binding for requirements, with no API work.

### Rules specific to this binding

1. Graph ids are derived from platform physical IDs, so re-syncs are idempotent.
2. A requirement's content hash covers its text, relation, value, unit and revision. A new platform revision therefore makes the graph approval stale until a route approves the new revision.
3. An agent never promotes maturity or completes a route task. Its output is a change action in draft, or a proposal object the graph's orchestrator has validated.
4. Computed status (SATISFIED, OPEN, ...) may be written back as a read-only attribute for display. It is labelled as computed and is never read back as input.

## Rhino/Grasshopper binding

Grasshopper becomes a tool behind the graph: part attributes flow in as inputs, measured values flow out as evidence. There is no PLM on this side, so the YAML system model stays the source of requirements, approvals and trace links.

### Two ways to connect

| Route | How it works | Fits when |
| --- | --- | --- |
| **Hops endpoint (recommended first)** | A small Python service built on `ghhops_server` exposes graph functions (read bound attributes, record a result, get status). The Hops component in a definition calls it like a function. | The designer works in Grasshopper and wants status on the canvas. Works on macOS too, because it is a plain REST call. |
| **Headless solve** | The graph runs a definition through Rhino.Compute: inputs from `Get` components, outputs from `Context Bake` / `Context Print`. Wrapped as a connector in the repo's connector layer, it runs like any external tool. | Batch sizing or verification runs driven by an agent. Rhino.Compute needs Windows. |

### Mapping

| Graph element | Grasshopper / Rhino side |
| --- | --- |
| `Part.attributes` (bound inputs) | `Get Number` / `Get String` components named exactly as the attribute; units in the component prompt |
| `Evidence` (measured outputs) | `Context Print` outputs named after the verification case and attribute |
| `Part` identity | Rhino object user text on the root object: `dg:id` and the model's content hash |
| `ToolDeclaration` | Rhino version, Grasshopper version and a hash of the `.gh` definition file |
| `OptimizationSpec` variables | Slider ranges; the graph's sizing controller sets values, the canvas does not |
| `ConsistencyCheck` | Compares a Grasshopper output (e.g. volume) with an independent value from another connector |

### Rules specific to this binding

1. The definition file is part of the tool declaration. Editing it changes its hash, which makes all evidence produced by it stale.
2. Units come from the Rhino document's unit system and are converted to the attribute's unit on the way in; a missing unit is an error, not a default.
3. A value typed on a slider is a design proposal, never evidence. Evidence only comes from a recorded solve with known inputs.
4. User text on Rhino objects carries ids and hashes only, never status, so a stale file cannot show a stale verdict.

## Authority and provenance across the boundary

A host sign-off becomes a graph approval only if a human made it and it names the exact revision; everything else stays a proposal. The table lists what crosses the boundary and what the graph checks before accepting it.

| Crossing | Accepted as | Check on the graph side |
| --- | --- | --- |
| 3DEXPERIENCE route approval on a requirement revision | `Approval` by a human `Principal` | The approver maps to a human principal with the approver role; the hash of the synced revision equals the pinned hash |
| Maturity promotion without a route | Nothing | Promotion alone records no named approver, so no approval is created |
| Simulation or test result from the platform | `Evidence` with provenance | Input revision, case and tool version are known; pass/fail is recomputed from the value, unit and uncertainty |
| Grasshopper solve via Hops or Rhino.Compute | `Evidence` with provenance | Definition-file hash and input values are recorded; the binding fills `ToolDeclaration` from Rhino and Grasshopper versions |
| Agent action in either host | Proposal only | Goes through the orchestrator's role-permission and validation gate, as over MCP |
| Computed status written back to a host | Display attribute | Never read back as input |

Two consequences follow. First, any change in the host (a new requirement revision, an edited definition, a new solver release) invalidates downstream evidence and approvals in the graph by the same hash rules the conformance vectors test. Second, the credibility of a host tool is still declared by the engineer, not assessed; the binding records tool versions, it does not vouch for them.

## Implementation path

Start with file exchange, then add live connections; each step is usable on its own.

1. **ReqIF round trip with 3DEXPERIENCE.** Export requirements from the platform, import them with `dg model import-cards`, assess. No API access needed.
2. **Hops endpoint for Grasshopper.** A `ghhops_server` service that reads bound attributes from the model and records solve results as evidence.
3. **Rhino.Compute connector.** The same definition run headless through the repository's connector layer (added in 0.3.0 with ten open-source CAx connectors, mostly untested against real installations; FreeCAD is tested through the FreeCAD MCP), so agents can drive sizing loops. The Rhino.Compute connector itself is not built yet.
4. **3DEXPERIENCE read sync.** REST read of engineering items, parameters, requirements and route approvals into the graph, keyed by physical ID.
5. **Event-driven re-assessment.** Subscribe to platform events and re-assess on change.
6. **Write-back of proposals.** Draft change actions and proposed parameter values, never promotions or approvals.

### Open questions

- Which requirement object and link types does the target tenant use, and are they exposed through the public web services or only through ReqIF?
- Does a route task record the exact object revision it approved, or only the object? Without a revision, hash pinning needs a sync-time snapshot.
- How are SIMULIA result values exposed for reading: as attributes on the simulation object, or only inside result files?
- Should the Grasshopper side ever hold its own copy of requirements, or always fetch them from the graph at solve time?

### Sources

- [3DEXPERIENCE Platform Openness](https://www.3ds.com/3dexperience-platform/openness.md): REST, event bus, authentication, exchange standards.
- [The Hops Component](https://developer.rhino3d.com/guides/compute/hops-component/): Hops, Rhino.Compute, `ghhops_server`, platform limits.
- REST namespace names (`dseng`, `dsreq`, `dslc`, `dschg`), the CSRF and security-context headers, and route and maturity behaviour are from prior knowledge and were not confirmed against a page; check them in the tenant's developer documentation.
