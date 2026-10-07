# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from design_graph import Graph, GraphError, GraphValidationError, Provisional, Ref, rule


class Inputs(BaseModel):
    force_N: float
    label: str


class Result(BaseModel):
    margin: float


calls = {"source": 0, "compute": 0}


@rule(version="1", source="synthetic test input")
def make_inputs() -> Inputs:
    calls["source"] += 1
    return Provisional(Inputs(force_N=10.0, label="invented"), "test assumption")


@rule(version="1", source="synthetic calculation")
def calculate(force_N: float) -> Result:
    calls["compute"] += 1
    return Result(margin=20.0 - force_N)


@rule(version="1", source="synthetic aggregate")
def sum_values(values: list[float]) -> float:
    return sum(values)


@rule(version="1", source="synthetic string operation")
def string_length(value: str) -> int:
    return len(value)


def make_graph(cache_dir: Path | None = None) -> Graph:
    return (
        Graph(cache_dir)
        .add("inputs", make_inputs, {})
        .add("margin", calculate, {"force_N": Ref("inputs", "force_N")})
    )


def test_ref_path_validation_and_evaluation() -> None:
    graph = make_graph()
    graph.check()
    assert graph.evaluate("margin")["margin"].margin == 10.0
    assert graph.provenance("margin").upstream == ["inputs"]


def test_unknown_node_and_bad_path_are_precise() -> None:
    graph = Graph().add("margin", calculate, {"force_N": Ref("missing", "force_N")})
    with pytest.raises(GraphValidationError, match="references unknown node 'missing'"):
        graph.check()

    graph = Graph().add("inputs", make_inputs, {}).add(
        "margin", calculate, {"force_N": Ref("inputs", "unknown")}
    )
    with pytest.raises(GraphValidationError, match="has no field 'unknown'"):
        graph.check()


def test_reference_type_mismatch_is_reported() -> None:
    graph = Graph().add("inputs", make_inputs, {}).add(
        "length", string_length, {"value": Ref("inputs", "force_N")}
    )
    with pytest.raises(GraphValidationError, match="expects str, but 'inputs'.force_N produces float"):
        graph.check()


def test_cycle_is_rejected() -> None:
    @rule(version="1", source="synthetic")
    def identity(value: float) -> float:
        return value

    graph = Graph().add("a", identity, {"value": Ref("b")}).add(
        "b", identity, {"value": Ref("a")}
    )
    with pytest.raises(GraphValidationError, match="contains a cycle"):
        graph.check()


def test_cache_hits_after_set_and_changes_with_inputs() -> None:
    calls.update(source=0, compute=0)
    graph = make_graph()
    graph.evaluate("margin")
    graph.evaluate("margin")
    assert calls == {"source": 1, "compute": 1}
    assert graph.provenance("margin").cache_hit is True

    graph.set("margin", force_N=12.0)
    assert graph.evaluate("margin")["margin"].margin == 8.0
    assert calls["compute"] == 2


def test_disk_cache_reuses_picklable_values(tmp_path: Path) -> None:
    calls.update(source=0, compute=0)
    make_graph(tmp_path).evaluate("margin")
    second = make_graph(tmp_path)
    second.evaluate("margin")
    assert second.provenance("margin").cache_hit is True
    assert calls == {"source": 1, "compute": 1}


def test_provisional_reasons_propagate_through_paths_and_lists() -> None:
    @rule(version="1", source="synthetic collector")
    def collect(values: list[float]) -> float:
        return sum(values)

    graph = (
        Graph()
        .add("inputs", make_inputs, {})
        .add("sum", collect, {"values": [Ref("inputs", "force_N"), Provisional(3.0, "manual value")]})
    )
    assert graph.evaluate("sum")["sum"] == 13.0
    assert graph.provenance("sum").provisional_reasons == ["test assumption", "manual value"]
    assert graph.assumptions() == {"test assumption": ["inputs"], "manual value": ["sum"]}


def test_provenance_and_serialization(tmp_path: Path) -> None:
    graph = make_graph()
    graph.evaluate()
    record = graph.provenance("margin")
    assert record.version == "1"
    assert record.source == "synthetic calculation"
    assert len(record.input_hash) == 64
    output = tmp_path / "graph.json"
    serialized = graph.to_json(output)
    assert output.read_text(encoding="utf-8") == serialized + "\n"
    assert '"value_preview"' in serialized
    assert '"test assumption"' in serialized


def test_target_evaluation_skips_unrelated_nodes() -> None:
    @rule(version="1", source="synthetic independent")
    def independent() -> str:
        raise AssertionError("unrequested node was evaluated")

    graph = make_graph().add("unused", independent, {})
    assert graph.evaluate("margin")["margin"].margin == 10.0
    with pytest.raises(GraphError, match="has not been evaluated"):
        graph.provenance("unused")


def test_set_rolls_back_invalid_bindings() -> None:
    graph = make_graph()
    with pytest.raises(GraphValidationError, match="expects float"):
        graph.set("margin", force_N="wrong")
    assert graph.evaluate("margin")["margin"].margin == 10.0