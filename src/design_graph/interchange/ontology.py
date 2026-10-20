# SPDX-License-Identifier: Apache-2.0
"""Formal ontology export: an OWL 2 vocabulary, SHACL shapes, and RDF data for a SystemModel.

The vocabulary and the shapes are generated from the Pydantic element classes, so they cannot drift
from the model. Elements become named individuals (`<base><id>`); nested values (quantities,
attestations, criteria, ...) become typed blank nodes; `dict` fields become `dg:Entry` nodes with a
key. Each active relationship is also written as a direct triple (`part dg:satisfy requirement`),
next to the reified `dg:Relationship` that keeps its id, rationale and withdrawn flag.

The shapes are never stricter than `validate_model`: a model without validation errors conforms.
Rules that SHACL Core cannot express stay in Python and are listed in `NOT_EXPRESSED`, which is
also written into the ontology header.
"""

from __future__ import annotations

import inspect
import types
import typing
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from rdflib import BNode, Graph, Literal, Namespace, URIRef
from rdflib.collection import Collection
from rdflib.namespace import DCTERMS, OWL, RDF, RDFS, SH, XSD
from rdflib.term import Node

from design_graph.model.elements import ElementId, _Element
from design_graph.model.system import SystemModel
from design_graph.model.validate import _ENDPOINTS

DG = Namespace("https://w3id.org/design-graph/ontology#")
DGSH = Namespace("https://w3id.org/design-graph/shapes#")
QUDT = Namespace("http://qudt.org/schema/qudt/")
ONTOLOGY_IRI = URIRef("https://w3id.org/design-graph/ontology")
SHAPES_IRI = URIRef("https://w3id.org/design-graph/shapes")
DEFAULT_BASE = "urn:design-graph:element:"
FORMATS = {"ttl": "turtle", "jsonld": "json-ld", "xml": "xml", "nt": "nt"}

NOT_EXPRESSED = (
    "Derived verification and satisfaction status (computed by `dg model assess`, not stored).",
    "Content-hash pinning of approvals, reviews and evidence, and the staleness it detects.",
    "Unit dimensions and conversions; units are exported as unit strings.",
    "Cycles in derive and refine relationships and in the part tree.",
    "Requirement rules that depend on the relation (target, upper, tolerance) and wording lints.",
    "Endpoint types of withdrawn relationships, and relationships touching withdrawn elements.",
    "Evidence linked to its case by a verify relationship; attestation roles of the principal.",
    "Trade-study decisions against scores, and resolution of consistency-check values.",
    "The order of list fields; lists are exported as unordered multi-valued properties.",
)

_ID_PATTERN: str = typing.get_args(ElementId)[1].pattern
_SCALARS: dict[type, URIRef] = {
    str: XSD.string,
    bool: XSD.boolean,
    int: XSD.integer,
    float: XSD.double,
}

# Field names that mean different things in different classes get their own property names.
_RENAMED = {
    ("Quantity", "value"): "numericValue",
    ("Statement", "source"): "sourceText",
    ("Allowable", "source"): "sourceText",
    ("ConsistencyCheck", "tolerance"): "relativeTolerance",
}
# A field holding either a nested value or a plain literal is split into two properties.
_SPLIT = {("Evidence", "value"): "booleanValue"}

# What each reference field points at (its OWL range). "Element" is any element.
_RANGES: dict[str, tuple[str, ...]] = {
    "analysis": ("VerificationCase",),
    "assumptions": ("Assumption",),
    "author": ("Principal",),
    "case": ("VerificationCase",),
    "compared_with": ("VerificationCase",),
    "concepts": ("Concept",),
    "constraints": ("FailureMode", "Requirement"),
    "covers": ("Requirement",),
    "created_by": ("Principal",),
    "decided_by": ("Principal",),
    "declared_by": ("Principal",),
    "derived_from": ("Requirement", "Need", "DesignRule"),
    "element": ("Element",),
    "elements": ("Element",),
    "impacts": ("Requirement",),
    "owner": ("Principal",),
    "parent": ("Part",),
    "principal": ("Principal",),
    "requirement": ("Requirement",),
    "requirements": ("Requirement",),
    "reviewer": ("Principal",),
    "selected": ("Concept",),
    "source": ("Element",),
    "subject": ("Part",),
    "target": ("Element",),
    "tool": ("ToolDeclaration",),
    "validated_by": ("Evidence",),
}
# References `validate_model` does not check; their shapes only require an IRI.
_UNCHECKED = {
    ("Assumption", "owner"),
    ("Baseline", "created_by"),
    ("ReviewFinding", "element"),
    ("ValueRef", "element"),
}
# (class, reference field, required role) whose principal must be a human, as validation demands.
_HUMAN_ONLY = (
    ("Approval", "principal", "approver"),
    ("ToolDeclaration", "declared_by", None),
    ("Attestation", "principal", None),
)
_RELATIONSHIP_KINDS = ("derive", "refine", "satisfy", "verify", "allocate", "trace")


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part.title() for part in rest)


def _doc(cls: type) -> str:
    text = inspect.getdoc(cls) or ""
    if cls.__doc__ is None or text == inspect.getdoc(BaseModel):
        return ""
    return " ".join(text.split("\n\n")[0].split())


@dataclass(frozen=True)
class _Field:
    owner: type[BaseModel]
    name: str
    prop: str
    kind: str  # "ref", "model", "literal", "map", "identifier"
    many: bool
    required: bool
    datatype: URIRef | None = None
    choices: tuple[str, ...] = ()
    target: type[BaseModel] | None = None
    ranges: tuple[str, ...] = ()

    @property
    def is_object(self) -> bool:
        return self.kind in ("ref", "model", "map")

    @property
    def checked(self) -> bool:
        return (self.owner.__name__, self.name) not in _UNCHECKED


def _strip(annotation: Any) -> tuple[Any, bool, list[Any]]:
    """Remove Optional and Annotated; return the bare type, whether it is a list, and metadata."""

    metadata: list[Any] = []
    many = False
    while True:
        origin = typing.get_origin(annotation)
        args = typing.get_args(annotation)
        if origin is typing.Annotated:
            annotation, metadata = args[0], metadata + list(args[1:])
        elif origin in (typing.Union, types.UnionType) and type(None) in args:
            rest = tuple(a for a in args if a is not type(None))
            annotation = rest[0] if len(rest) == 1 else typing.Union[rest]  # noqa: UP007
        elif origin is list and not many:
            annotation, many = args[0], True
        else:
            return annotation, many, metadata


def _is_ref(metadata: list[Any]) -> bool:
    return any(getattr(m, "pattern", None) == _ID_PATTERN for m in metadata)


def _fields(cls: type[BaseModel]) -> list[_Field]:
    out: list[_Field] = []
    for name, info in cls.model_fields.items():
        bare, many, metadata = _strip(info.annotation)
        metadata += list(info.metadata)
        prop = _RENAMED.get((cls.__name__, name), _camel(name))
        required = info.is_required()

        def field(
            prop: str,
            kind: str,
            required: bool = required,
            name: str = name,
            many: bool = many,
            **kw: Any,
        ) -> _Field:
            return _Field(cls, name, prop, kind, many, required, **kw)

        origin = typing.get_origin(bare)
        if name == "id":
            out.append(field("identifier", "identifier", datatype=XSD.string))
        elif _is_ref(metadata):
            if name not in _RANGES:
                raise ValueError(f"no ontology range for reference field {cls.__name__}.{name}")
            out.append(field(prop, "ref", ranges=_RANGES[name]))
        elif origin is dict:
            out.append(field(prop, "map"))
        elif origin is typing.Literal:
            choices = tuple(str(c) for c in typing.get_args(bare))
            out.append(field(prop, "literal", datatype=XSD.string, choices=choices))
        elif isinstance(bare, type) and issubclass(bare, BaseModel):
            out.append(field(prop, "model", target=bare))
        elif bare in _SCALARS:
            out.append(field(prop, "literal", datatype=_SCALARS[bare]))
        elif origin in (typing.Union, types.UnionType):
            split = _SPLIT.get((cls.__name__, name))
            parts = [_strip(a)[0] for a in typing.get_args(bare)]
            models = [p for p in parts if isinstance(p, type) and issubclass(p, BaseModel)]
            scalars = [p for p in parts if p in _SCALARS]
            if split is None or len(models) != 1 or len(scalars) != 1:
                raise ValueError(f"no ontology mapping for {cls.__name__}.{name}: {bare}")
            out.append(field(prop, "model", target=models[0]))
            out.append(field(split, "literal", False, datatype=_SCALARS[scalars[0]]))
        else:
            raise ValueError(f"no ontology mapping for {cls.__name__}.{name}: {bare}")
    return out


def _classes() -> tuple[list[type[BaseModel]], list[type[BaseModel]]]:
    """Element classes in collection order, then the nested value classes they use."""

    elements: list[type[BaseModel]] = []
    for name, info in SystemModel.model_fields.items():
        if name != "format_version":
            elements.append(typing.get_args(info.annotation)[0])
    nested: list[type[BaseModel]] = []
    pending = list(elements)
    while pending:
        for f in _fields(pending.pop(0)):
            if f.target is not None and f.target not in elements + nested:
                nested.append(f.target)
                pending.append(f.target)
    return elements, nested


def _properties() -> dict[str, list[_Field]]:
    """Every property with the fields that use it; a property is either object or datatype."""

    elements, nested = _classes()
    props: dict[str, list[_Field]] = {}
    for cls in elements + nested:
        for f in _fields(cls):
            props.setdefault(f.prop, []).append(f)
    for prop, uses in props.items():
        if len({u.is_object for u in uses}) > 1 or len({u.datatype for u in uses}) > 1:
            where = ", ".join(f"{u.owner.__name__}.{u.name}" for u in uses)
            raise ValueError(f"property dg:{prop} has conflicting types ({where})")
    return props


def _list(graph: Graph, items: list[Node]) -> BNode:
    head = BNode()
    Collection(graph, head, items)
    return head


def _class_axiom(g: Graph, subject: Node, axis: URIRef, names: tuple[str, ...]) -> None:
    """`subject axis Class`, or an owl:unionOf the classes when there are several."""

    if len(names) == 1:
        g.add((subject, axis, DG[names[0]]))
        return
    union = BNode()
    g.add((union, RDF.type, OWL.Class))
    g.add((union, OWL.unionOf, _list(g, [DG[n] for n in names])))
    g.add((subject, axis, union))


def _bind(graph: Graph) -> Graph:
    graph.bind("dg", DG)
    graph.bind("dgsh", DGSH)
    graph.bind("qudt", QUDT)
    graph.bind("owl", OWL)
    graph.bind("sh", SH)
    graph.bind("dcterms", DCTERMS)
    return graph


def _version() -> str:
    try:
        return version("agentic-engineering-design-ontology")
    except PackageNotFoundError:  # pragma: no cover - running from a source tree
        return "0"


# ---------------------------------------------------------------------- vocabulary


def ontology_graph() -> Graph:
    """The OWL 2 vocabulary: classes, object and datatype properties, relationship properties."""

    g = _bind(Graph())
    g.add((ONTOLOGY_IRI, RDF.type, OWL.Ontology))
    g.add((ONTOLOGY_IRI, OWL.versionInfo, Literal(_version())))
    g.add((ONTOLOGY_IRI, DCTERMS.title, Literal("Agentic Engineering Design Ontology", lang="en")))
    g.add((ONTOLOGY_IRI, DCTERMS.license, URIRef("https://www.apache.org/licenses/LICENSE-2.0")))
    g.add(
        (
            ONTOLOGY_IRI,
            DCTERMS.description,
            Literal(
                "MBSE system model for engineering design: needs, parts, requirements, "
                "verification cases, evidence, approvals and engineering elements, generated "
                "from the design_graph.model classes.",
                lang="en",
            ),
        )
    )
    g.add(
        (
            ONTOLOGY_IRI,
            RDFS.comment,
            Literal("Not expressed in OWL or SHACL: " + " ".join(NOT_EXPRESSED), lang="en"),
        )
    )
    elements, nested = _classes()
    g.add((DG.Element, RDF.type, OWL.Class))
    g.add((DG.Element, RDFS.label, Literal("Element", lang="en")))
    g.add((DG.Element, RDFS.comment, Literal("Anything with a model-wide id.", lang="en")))
    for cls in elements + nested:
        iri = DG[cls.__name__]
        g.add((iri, RDF.type, OWL.Class))
        g.add((iri, RDFS.label, Literal(cls.__name__, lang="en")))
        if cls in elements:
            g.add((iri, RDFS.subClassOf, DG.Element))
        if _doc(cls):
            g.add((iri, RDFS.comment, Literal(_doc(cls), lang="en")))
    g.add((DG.Quantity, RDFS.subClassOf, QUDT.QuantityValue))
    g.add((DG.numericValue, RDFS.subPropertyOf, QUDT.numericValue))
    g.add((DG.name, RDFS.subPropertyOf, RDFS.label))

    g.add((DG.Entry, RDF.type, OWL.Class))
    g.add((DG.Entry, RDFS.comment, Literal("One key of a mapping field.", lang="en")))
    for prop, ptype, rng in (
        ("entryKey", OWL.DatatypeProperty, XSD.string),
        ("entryValue", OWL.DatatypeProperty, RDFS.Literal),
        ("entryObject", OWL.ObjectProperty, None),
        ("entry", OWL.ObjectProperty, DG.Entry),
    ):
        g.add((DG[prop], RDF.type, ptype))
        g.add((DG[prop], RDFS.domain, DG.Entry))
        if rng is not None:
            g.add((DG[prop], RDFS.range, rng))

    base = set(_Element.model_fields)
    for prop, uses in sorted(_properties().items()):
        iri = DG[prop]
        first = uses[0]
        g.add((iri, RDF.type, OWL.ObjectProperty if first.is_object else OWL.DatatypeProperty))
        g.add((iri, RDFS.label, Literal(prop, lang="en")))
        owners = {u.owner for u in uses}
        if first.name in base and all(issubclass(o, _Element) for o in owners):
            g.add((iri, RDFS.domain, DG.Element))
        elif len(owners) == 1:
            g.add((iri, RDFS.domain, DG[first.owner.__name__]))
        if first.kind == "ref" and len({u.ranges for u in uses}) == 1:
            _class_axiom(g, iri, RDFS.range, first.ranges)
        elif first.kind == "model" and len({u.target for u in uses}) == 1:
            assert first.target is not None
            g.add((iri, RDFS.range, DG[first.target.__name__]))
        elif first.kind == "map":
            g.add((iri, RDFS.range, DG.Entry))
        elif first.datatype is not None:
            g.add((iri, RDFS.range, first.datatype))

    for kind in _RELATIONSHIP_KINDS:
        iri = DG[kind]
        g.add((iri, RDF.type, OWL.ObjectProperty))
        g.add((iri, RDFS.label, Literal(kind, lang="en")))
        g.add(
            (
                iri,
                RDFS.comment,
                Literal(
                    f"Source {kind} target; written for each active {kind} Relationship.",
                    lang="en",
                ),
            )
        )
        if kind != "trace":
            g.add((iri, RDFS.subPropertyOf, DG.trace))
        if kind in _ENDPOINTS:
            sources, targets = _ENDPOINTS[kind]
            _class_axiom(g, iri, RDFS.domain, tuple(c.__name__ for c in sources))
            _class_axiom(g, iri, RDFS.range, tuple(c.__name__ for c in targets))
    return g


# ---------------------------------------------------------------------- shapes


def _class_constraint(g: Graph, node: Node, classes: tuple[str, ...]) -> None:
    if len(classes) == 1:
        g.add((node, SH["class"], DG[classes[0]]))
        return
    options: list[Node] = []
    for name in classes:
        option = BNode()
        g.add((option, SH["class"], DG[name]))
        options.append(option)
    g.add((node, SH["or"], _list(g, options)))


def shapes_graph() -> Graph:
    """SHACL Core shapes mirroring the structural part of `validate_model`."""

    g = _bind(Graph())
    g.add((SHAPES_IRI, RDF.type, OWL.Ontology))
    g.add((SHAPES_IRI, OWL.imports, ONTOLOGY_IRI))
    g.add((SHAPES_IRI, OWL.versionInfo, Literal(_version())))
    elements, nested = _classes()
    for cls in elements + nested:
        shape = DGSH[f"{cls.__name__}Shape"]
        g.add((shape, RDF.type, SH.NodeShape))
        g.add((shape, SH.targetClass, DG[cls.__name__]))
        for f in _fields(cls):
            prop = DGSH[f"{cls.__name__}-{f.prop}"]
            g.add((shape, SH.property, prop))
            g.add((prop, SH.path, DG[f.prop]))
            if f.required and not f.many and f.kind != "map":
                g.add((prop, SH.minCount, Literal(1)))
            if not f.many and f.kind != "map":
                g.add((prop, SH.maxCount, Literal(1)))
            if f.kind == "identifier":
                g.add((prop, SH.datatype, XSD.string))
                g.add((prop, SH.pattern, Literal(_ID_PATTERN)))
            elif f.kind == "literal":
                assert f.datatype is not None
                g.add((prop, SH.datatype, f.datatype))
                if f.choices:
                    g.add(
                        (
                            prop,
                            SH["in"],
                            _list(g, [Literal(c, datatype=XSD.string) for c in f.choices]),
                        )
                    )
            elif f.kind == "ref":
                g.add((prop, SH.nodeKind, SH.IRI))
                if f.checked:
                    _class_constraint(g, prop, f.ranges)
            elif f.kind == "model":
                assert f.target is not None
                g.add((prop, SH["class"], DG[f.target.__name__]))
            else:
                g.add((prop, SH["class"], DG.Entry))
    for kind, (sources, targets) in _ENDPOINTS.items():
        shape = DGSH[f"{kind}EndpointsShape"]
        g.add((shape, RDF.type, SH.NodeShape))
        g.add((shape, SH.targetSubjectsOf, DG[kind]))
        _class_constraint(g, shape, tuple(c.__name__ for c in sources))
        prop = DGSH[f"{kind}-target"]
        g.add((shape, SH.property, prop))
        g.add((prop, SH.path, DG[kind]))
        _class_constraint(g, prop, tuple(c.__name__ for c in targets))
    for cls_name, field, role in _HUMAN_ONLY:
        shape = DGSH[f"{cls_name}HumanShape"]
        g.add((shape, RDF.type, SH.NodeShape))
        g.add((shape, SH.targetClass, DG[cls_name]))
        checks = [("kind", "human")] + ([("roles", role)] if role else [])
        for prop_name, value in checks:
            prop = DGSH[f"{cls_name}-{_camel(field)}-{prop_name}"]
            g.add((shape, SH.property, prop))
            g.add((prop, SH.path, _list(g, [DG[_camel(field)], DG[prop_name]])))
            g.add((prop, SH.hasValue, Literal(value, datatype=XSD.string)))
    review = DGSH.ReviewIndependenceShape
    g.add((review, RDF.type, SH.NodeShape))
    g.add((review, SH.targetClass, DG.Review))
    for name, prop_name, constraint, value in (
        ("independent", "reviewer", SH.disjoint, DG.author),
        ("role", "reviewer", None, Literal("reviewer", datatype=XSD.string)),
    ):
        prop = DGSH[f"Review-{name}"]
        g.add((review, SH.property, prop))
        if constraint is not None:
            g.add((prop, SH.path, DG[prop_name]))
            g.add((prop, constraint, value))
        else:
            g.add((prop, SH.path, _list(g, [DG.reviewer, DG.roles])))
            g.add((prop, SH.hasValue, value))
    return g


# ---------------------------------------------------------------------- data


class _Writer:
    def __init__(self, base: str) -> None:
        self.base = base
        self.graph = _bind(Graph())
        self.graph.bind("el", Namespace(base))

    def iri(self, element_id: str) -> URIRef:
        return URIRef(self.base + element_id)

    def node(self, subject: Node, value: BaseModel) -> None:
        for f in _fields(type(value)):
            raw = getattr(value, f.name)
            if raw is None or (raw in ("", [], {}) and not f.required):
                continue
            if isinstance(raw, list):
                for item in raw:
                    self.value(subject, f, item)
            else:
                self.value(subject, f, raw)

    def value(self, subject: Node, f: _Field, raw: Any) -> None:
        g = self.graph
        if f.kind == "identifier":
            g.add((subject, DG.identifier, Literal(raw, datatype=XSD.string)))
        elif f.kind == "ref":
            g.add((subject, DG[f.prop], self.iri(raw)))
        elif f.kind == "map":
            self.entries(subject, DG[f.prop], raw)
        elif isinstance(raw, BaseModel):
            if f.kind != "model":
                return  # the literal half of a split field
            child = BNode()
            g.add((subject, DG[f.prop], child))
            g.add((child, RDF.type, DG[type(raw).__name__]))
            self.node(child, raw)
        elif f.kind == "literal" and f.datatype is not None:
            g.add((subject, DG[f.prop], Literal(raw, datatype=f.datatype)))

    def entries(self, subject: Node, prop: URIRef, mapping: dict[str, Any]) -> None:
        g = self.graph
        for key in sorted(mapping):
            entry = BNode()
            g.add((subject, prop, entry))
            g.add((entry, RDF.type, DG.Entry))
            g.add((entry, DG.entryKey, Literal(key, datatype=XSD.string)))
            value = mapping[key]
            for item in value if isinstance(value, list) else [value]:
                if isinstance(item, dict):
                    self.entries(entry, DG.entry, item)
                elif isinstance(item, BaseModel):
                    child = BNode()
                    g.add((entry, DG.entryObject, child))
                    g.add((child, RDF.type, DG[type(item).__name__]))
                    self.node(child, item)
                else:
                    g.add((entry, DG.entryValue, Literal(item)))


def model_graph(model: SystemModel, base: str = DEFAULT_BASE) -> Graph:
    """RDF data for one model: one individual per element plus direct relationship triples."""

    writer = _Writer(base)
    g = writer.graph
    for name in SystemModel.model_fields:
        if name == "format_version":
            continue
        for element in getattr(model, name):
            subject = writer.iri(element.id)
            g.add((subject, RDF.type, DG[type(element).__name__]))
            g.add((subject, RDF.type, DG.Element))
            writer.node(subject, element)
    for rel in model.relationships:
        if not rel.withdrawn:
            g.add((writer.iri(rel.source), DG[rel.kind], writer.iri(rel.target)))
    return g


# ---------------------------------------------------------------------- files and checks


_HEADER = (
    "# SPDX-License-Identifier: Apache-2.0\n"
    "# Generated by design_graph.interchange.ontology; do not edit.\n"
)


def serialize(graph: Graph, fmt: str = "ttl") -> str:
    text = graph.serialize(format=FORMATS[fmt])
    return _HEADER + text if fmt == "ttl" else text


def write_ontology(directory: str | Path, fmt: str = "ttl") -> list[Path]:
    """Write the vocabulary and the shapes; return the written paths."""

    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    paths = [out / f"design-graph.{fmt}", out / f"design-graph-shapes.{fmt}"]
    for path, graph in zip(paths, (ontology_graph(), shapes_graph()), strict=True):
        path.write_text(serialize(graph, fmt), encoding="utf-8")
    return paths


def check_conformance(data: Graph) -> tuple[bool, str]:
    """Validate RDF data against the shapes with pySHACL (optional `ontology` extra)."""

    from pyshacl import validate

    conforms, _, text = validate(
        data, shacl_graph=shapes_graph(), ont_graph=ontology_graph(), inference="none"
    )
    return bool(conforms), str(text)
