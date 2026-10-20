# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

pytest.importorskip("rdflib")
pytest.importorskip("pyshacl")

from click.testing import CliRunner  # noqa: E402
from rdflib import Graph, Literal, Namespace, URIRef  # noqa: E402
from rdflib.compare import isomorphic  # noqa: E402
from rdflib.namespace import OWL, RDF, RDFS, XSD  # noqa: E402

from design_graph.cli import main  # noqa: E402
from design_graph.interchange import ontology  # noqa: E402
from design_graph.interchange.ontology import (  # noqa: E402
    DEFAULT_BASE,
    DG,
    check_conformance,
    model_graph,
    ontology_graph,
    shapes_graph,
)
from design_graph.model import (  # noqa: E402
    Relationship,
    SystemModel,
    load_model,
    validate_model,
)
from design_graph.model.engineering import Review  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BRACKET = ROOT / "examples" / "model_process_bracket.yaml"
ENCLOSURE = ROOT / "examples" / "model_enclosure.yaml"
EL = Namespace(DEFAULT_BASE)


def test_vocabulary_is_owl_and_typed() -> None:
    g = ontology_graph()
    objects = set(g.subjects(RDF.type, OWL.ObjectProperty))
    datatypes = set(g.subjects(RDF.type, OWL.DatatypeProperty))
    assert objects and datatypes and not objects & datatypes
    for name in ("Requirement", "Part", "VerificationCase", "Evidence", "Approval", "TradeStudy"):
        assert (DG[name], RDFS.subClassOf, DG.Element) in g
    assert (DG.satisfy, RDFS.subPropertyOf, DG.trace) in g
    assert (DG.subject, RDFS.range, DG.Part) in g
    assert (DG.Quantity, RDFS.subClassOf, URIRef("http://qudt.org/schema/qudt/QuantityValue")) in g


def test_checked_in_files_are_current() -> None:
    for name, graph in (
        ("design-graph.ttl", ontology_graph()),
        ("design-graph-shapes.ttl", shapes_graph()),
    ):
        committed = Graph().parse(ROOT / "ontology" / name, format="turtle")
        assert isomorphic(committed, graph), f"ontology/{name} is stale; run 'dg ontology export'"


def test_property_conflicts_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    renamed = dict(ontology._RENAMED)
    del renamed[("Statement", "source")]
    monkeypatch.setattr(ontology, "_RENAMED", renamed)
    with pytest.raises(ValueError, match="dg:source has conflicting types"):
        ontology_graph()


@pytest.mark.parametrize("path", [BRACKET, ENCLOSURE])
def test_examples_conform_and_are_complete(path: Path) -> None:
    model = load_model(path)
    data = model_graph(model)
    conforms, report = check_conformance(data)
    assert conforms, report
    for collection, elements in model:
        if isinstance(elements, list) and elements:
            cls = DG[type(elements[0]).__name__]
            assert len(set(data.subjects(RDF.type, cls))) == len(elements), collection
    declared = set(ontology_graph().subjects())
    used = {p for p in data.predicates() if p != RDF.type}
    assert used <= declared, used - declared


def test_relationships_are_reified_and_direct() -> None:
    model = load_model(BRACKET)
    model.relationships.append(
        Relationship(id="old", kind="trace", source="R1", target="S1", withdrawn=True)
    )
    data = model_graph(model)
    assert (EL.SYS, DG.satisfy, EL.R1) in data
    assert (EL.r1, DG.kind, Literal("refine", datatype=XSD.string)) in data
    assert (EL.old, DG.withdrawn, Literal(True)) in data
    assert (EL.R1, DG.trace, EL.S1) not in data


def test_formats_round_trip() -> None:
    data = model_graph(load_model(ENCLOSURE))
    for fmt, name in (("jsonld", "json-ld"), ("xml", "xml"), ("nt", "nt")):
        again = Graph().parse(data=ontology.serialize(data, fmt), format=name)
        assert isomorphic(again, data), fmt


def _set_subject_to_need(m: SystemModel) -> None:
    m.requirements[0].subject = "N1"


def _requirement_satisfies(m: SystemModel) -> None:
    m.relationships.append(Relationship(id="bad", kind="satisfy", source="R1", target="R1"))


def _agent_approves(m: SystemModel) -> None:
    m.approvals[0].principal = "agent"


def _agent_declares_tool(m: SystemModel) -> None:
    m.tools[0].declared_by = "author-bot"


def _self_review(m: SystemModel) -> None:
    m.reviews.append(
        Review(id="RV", elements=["R1"], reviewer="qa-bot", author="qa-bot", date="2026-02-01")
    )


def _dangling_case(m: SystemModel) -> None:
    m.failure_modes[0].analysis = ["NOPE"]


@pytest.mark.parametrize(
    ("path", "mutate"),
    [
        (BRACKET, _set_subject_to_need),
        (BRACKET, _requirement_satisfies),
        (ENCLOSURE, _agent_approves),
        (BRACKET, _agent_declares_tool),
        (BRACKET, _self_review),
        (BRACKET, _dangling_case),
    ],
)
def test_broken_models_fail_both_checks(path: Path, mutate: Callable[[SystemModel], None]) -> None:
    model = load_model(path)
    mutate(model)
    assert any(f.severity == "error" for f in validate_model(model))
    conforms, _ = check_conformance(model_graph(model))
    assert not conforms


@pytest.mark.parametrize(
    "edit",
    [
        lambda g: g.set((EL.C1, DG.method, Literal("X", datatype=XSD.string))),
        lambda g: g.remove((EL.R1, DG.text, None)),
        lambda g: g.set((EL.R1, DG.identifier, Literal("-bad id", datatype=XSD.string))),
    ],
)
def test_edited_data_fails_shapes(edit: Callable[[Graph], None]) -> None:
    data = model_graph(load_model(BRACKET))
    edit(data)
    conforms, _ = check_conformance(data)
    assert not conforms


def test_cli_exports(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["ontology", "export", "-o", str(tmp_path / "onto")])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "onto" / "design-graph-shapes.ttl").is_file()
    out = tmp_path / "bracket.ttl"
    result = runner.invoke(main, ["model", "export-rdf", str(BRACKET), "-o", str(out), "--check"])
    assert result.exit_code == 0, result.output
    assert "SHACL: conforms" in result.output
    assert (EL.R1, RDF.type, DG.Requirement) in Graph().parse(out, format="turtle")
