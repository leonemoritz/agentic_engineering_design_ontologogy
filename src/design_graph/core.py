# SPDX-License-Identifier: Apache-2.0
"""Domain-neutral graph evaluation, validation, caching, and provenance."""

from __future__ import annotations

import hashlib
import inspect
import json
import pickle
import tempfile
import time
from collections import defaultdict, deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generic, TypeVar, Union, get_args, get_origin, get_type_hints

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class GraphError(Exception):
    """Base class for graph errors."""


class GraphValidationError(GraphError):
    """Raised when graph wiring is invalid."""


class Ref(BaseModel):
    """Reference a node result or a field within that result."""

    model_config = ConfigDict(frozen=True)

    node: str
    path: str = ""

    def __init__(self, node: str, path: str = "", **data: Any) -> None:
        super().__init__(node=node, path=path, **data)


class Provisional(BaseModel):
    """Mark a value as assumption-based and retain the reason."""

    value: Any
    reason: str

    def __init__(self, value: Any, reason: str, **data: Any) -> None:
        super().__init__(value=value, reason=reason, **data)

class Sourced(BaseModel, Generic[T]):
    """Attach requirement lineage and provisional reasons to a value."""

    value: T
    sources: list[str]
    provisional: list[str] = Field(default_factory=list)

    def __init__(
        self, value: Any, sources: list[str], provisional: list[str] | None = None, **data: Any
    ) -> None:
        super().__init__(value=value, sources=sources, provisional=provisional or [], **data)


class ProvenanceRecord(BaseModel):
    """Audit record for one graph evaluation."""

    node: str
    function: str
    version: str
    source: str
    input_hash: str
    upstream: list[str]
    cache_hit: bool
    wall_time_s: float
    provisional_reasons: list[str]
    introduced_reasons: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class RuleMetadata:
    version: str
    source: str


@dataclass
class _Node:
    name: str
    function: Callable[..., Any]
    bindings: dict[str, Any]
    metadata: RuleMetadata
    signature: inspect.Signature
    hints: dict[str, Any]
    return_type: Any
    stage: str | None = None


@dataclass
class _CachedResult:
    value: Any
    introduced_reasons: list[str]


def rule(*, version: str, source: str) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Attach cache-version and citation metadata to a pure graph function."""

    if not version.strip() or not source.strip():
        raise ValueError("rule version and source must be non-empty")

    def decorate(function: Callable[..., T]) -> Callable[..., T]:
        setattr(function, "__design_graph_rule__", RuleMetadata(version=version, source=source))
        return function

    return decorate


class Graph:
    """A directed acyclic graph of pure functions and explicit bindings."""

    def __init__(self, cache_dir: str | Path | None = None) -> None:
        self._nodes: dict[str, _Node] = {}
        self._values: dict[str, Any] = {}
        self._reasons: dict[str, list[str]] = {}
        self._lineage: dict[str, list[str]] = {}
        self._provenance: dict[str, ProvenanceRecord] = {}
        self._memory_cache: dict[str, _CachedResult] = {}
        self._cache_dir = Path(cache_dir) if cache_dir is not None else None
        if self._cache_dir is not None:
            self._cache_dir.mkdir(parents=True, exist_ok=True)

    def add(
        self,
        name: str,
        function: Callable[..., Any],
        bindings: Mapping[str, Any],
        *,
        stage: str | None = None,
    ) -> Graph:
        """Add a named function and its parameter bindings."""

        if name in self._nodes:
            raise GraphValidationError(f"node {name!r} already exists")
        if not name:
            raise GraphValidationError("node name must be non-empty")
        metadata = getattr(function, "__design_graph_rule__", None)
        if not isinstance(metadata, RuleMetadata):
            raise GraphValidationError(f"function for node {name!r} is missing @rule metadata")
        try:
            signature = inspect.signature(function)
            hints = get_type_hints(function)
        except (TypeError, NameError) as error:
            raise GraphValidationError(f"cannot inspect function for node {name!r}: {error}") from error
        if "return" not in hints:
            raise GraphValidationError(f"function for node {name!r} must declare a return type")
        self._nodes[name] = _Node(
            name=name,
            function=function,
            bindings=dict(bindings),
            metadata=metadata,
            signature=signature,
            hints=hints,
            return_type=hints["return"],
            stage=stage,
        )
        return self

    def check(self) -> None:
        """Validate references, field paths, binding names, and graph acyclicity."""

        for node in self._nodes.values():
            parameters = node.signature.parameters
            extra = sorted(set(node.bindings) - set(parameters))
            if extra:
                raise GraphValidationError(
                    f"node {node.name!r} has binding for unknown parameter {extra[0]!r}"
                )
            for parameter_name, parameter in parameters.items():
                if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
                    continue
                if parameter_name not in node.bindings and parameter.default is inspect.Parameter.empty:
                    raise GraphValidationError(
                        f"node {node.name!r} is missing binding for parameter {parameter_name!r}"
                    )
                if parameter_name in node.bindings:
                    expected = node.hints.get(parameter_name, Any)
                    self._check_binding(node.name, parameter_name, node.bindings[parameter_name], expected)
        self._topological_order()

    def _check_binding(self, consumer: str, parameter: str, binding: Any, expected: Any) -> None:
        if isinstance(binding, Ref):
            if binding.node not in self._nodes:
                raise GraphValidationError(
                    f"node {consumer!r} parameter {parameter!r} references unknown node {binding.node!r}"
                )
            produced = self._resolve_path_type(binding.node, binding.path)
            expected_type = _unwrap_annotation(expected)
            if not _types_compatible(produced, expected_type):
                path = f".{binding.path}" if binding.path else ""
                raise GraphValidationError(
                    f"node {consumer!r} parameter {parameter!r} expects {_type_name(expected_type)}, "
                    f"but {binding.node!r}{path} produces {_type_name(produced)}"
                )
            return
        if isinstance(binding, Provisional):
            if not _value_matches_type(binding.value, expected):
                raise GraphValidationError(
                    f"node {consumer!r} parameter {parameter!r} expects {_type_name(expected)}, "
                    f"got {_type_name(type(binding.value))}"
                )
            return
        if isinstance(binding, Sourced):
            self._check_binding(consumer, parameter, binding.value, expected)
            return
        if isinstance(binding, (list, tuple)):
            element_type = _sequence_element_type(expected)
            for item in binding:
                self._check_binding(consumer, parameter, item, element_type)
            return
        if not _value_matches_type(binding, expected):
            raise GraphValidationError(
                f"node {consumer!r} parameter {parameter!r} expects {_type_name(expected)}, "
                f"got {_type_name(type(binding))}"
            )

    def _resolve_path_type(self, node_name: str, path: str) -> Any:
        result_type = self._nodes[node_name].return_type
        if not path:
            return result_type
        for field_name in path.split("."):
            result_type = _unwrap_annotation(result_type)
            if not inspect.isclass(result_type) or not issubclass(result_type, BaseModel):
                raise GraphValidationError(
                    f"reference path {node_name!r}.{path} cannot resolve field {field_name!r}: "
                    f"{_type_name(result_type)} is not a Pydantic model"
                )
            fields = getattr(result_type, "model_fields", {})
            if field_name not in fields:
                raise GraphValidationError(
                    f"reference path {node_name!r}.{path} has no field {field_name!r} "
                    f"on {_type_name(result_type)}"
                )
            result_type = fields[field_name].annotation
        return result_type

    def _topological_order(self, targets: Sequence[str] | None = None) -> list[str]:
        needed = set(self._nodes)
        if targets is not None:
            needed = set()
            pending = list(targets)
            while pending:
                name = pending.pop()
                if name not in self._nodes:
                    raise GraphValidationError(f"unknown target node {name!r}")
                if name in needed:
                    continue
                needed.add(name)
                pending.extend(self._dependencies(name))

        indegree = {name: 0 for name in needed}
        downstream: dict[str, list[str]] = defaultdict(list)
        for name in needed:
            for dependency in self._dependencies(name):
                if dependency in needed:
                    indegree[name] += 1
                    downstream[dependency].append(name)
        ready = deque(name for name in self._nodes if name in needed and indegree[name] == 0)
        order: list[str] = []
        while ready:
            name = ready.popleft()
            order.append(name)
            for consumer in downstream[name]:
                indegree[consumer] -= 1
                if indegree[consumer] == 0:
                    ready.append(consumer)
        if len(order) != len(needed):
            cycle_nodes = sorted(name for name, degree in indegree.items() if degree)
            raise GraphValidationError(f"graph contains a cycle involving {', '.join(cycle_nodes)}")
        return order

    def _dependencies(self, name: str) -> list[str]:
        dependencies: list[str] = []
        for binding in self._nodes[name].bindings.values():
            dependencies.extend(_refs_in(binding))
        return list(dict.fromkeys(dependencies))

    def evaluate(self, targets: str | Sequence[str] | None = None) -> dict[str, Any]:
        """Evaluate all nodes or only the ancestors of one or more targets."""

        if targets is None:
            requested = list(self._nodes)
        elif isinstance(targets, str):
            requested = [targets]
        else:
            requested = list(targets)
        self.check()
        for name in self._topological_order(requested):
            self._evaluate_node(name)
        return {name: self._values[name] for name in requested}

    def _evaluate_node(self, name: str) -> None:
        node = self._nodes[name]
        inputs: dict[str, Any] = {}
        reasons: list[str] = []
        requirement_ids: list[str] = []
        upstream = self._dependencies(name)
        for dependency in upstream:
            reasons.extend(self._reasons.get(dependency, []))
        for parameter_name, binding in node.bindings.items():
            resolved, binding_reasons, _ = self._resolve_binding(binding)
            inputs[parameter_name] = resolved
            reasons.extend(binding_reasons)
            requirement_ids.extend(self._requirement_sources(binding))
        reasons = list(dict.fromkeys(reasons))
        function_id = f"{node.function.__module__}.{node.function.__qualname__}"
        input_hash = _digest(
            {
                "function": function_id,
                "version": node.metadata.version,
                "inputs": inputs,
            }
        )
        cached = self._load_cache(input_hash)
        cache_hit = cached is not None
        started = time.perf_counter()
        if cached is None:
            output = node.function(**inputs)
            introduced: list[str] = []
            if isinstance(output, Provisional):
                introduced.append(output.reason)
                output = output.value
            cached = _CachedResult(output, introduced)
            self._store_cache(input_hash, cached)
        wall_time = time.perf_counter() - started
        reasons.extend(cached.introduced_reasons)
        reasons = list(dict.fromkeys(reasons))
        self._values[name] = cached.value
        self._reasons[name] = reasons
        requirement_ids.extend(_sources_in(cached.value))
        self._lineage[name] = list(dict.fromkeys(requirement_ids))
        self._provenance[name] = ProvenanceRecord(
            node=name,
            function=function_id,
            version=node.metadata.version,
            source=node.metadata.source,
            input_hash=input_hash,
            upstream=upstream,
            cache_hit=cache_hit,
            wall_time_s=wall_time,
            provisional_reasons=reasons,
            introduced_reasons=cached.introduced_reasons,
        )

    def _resolve_binding(self, binding: Any) -> tuple[Any, list[str], list[str]]:
        if isinstance(binding, Ref):
            value = self._values[binding.node]
            if binding.path:
                for part in binding.path.split("."):
                    value = getattr(value, part)
            return value, list(self._reasons.get(binding.node, [])), [binding.node]
        if isinstance(binding, Provisional):
            return binding.value, [binding.reason], []
        if isinstance(binding, Sourced):
            return binding.value, list(binding.provisional), []
        if isinstance(binding, list):
            values: list[Any] = []
            reasons: list[str] = []
            upstream: list[str] = []
            for item in binding:
                value, item_reasons, item_upstream = self._resolve_binding(item)
                values.append(value)
                reasons.extend(item_reasons)
                upstream.extend(item_upstream)
            return values, reasons, upstream
        if isinstance(binding, tuple):
            values = []
            reasons = []
            upstream = []
            for item in binding:
                value, item_reasons, item_upstream = self._resolve_binding(item)
                values.append(value)
                reasons.extend(item_reasons)
                upstream.extend(item_upstream)
            return tuple(values), reasons, upstream
        return binding, [], []

    def _requirement_sources(self, binding: Any) -> list[str]:
        if isinstance(binding, Ref):
            value = self._values[binding.node]
            sources: list[str] = []
            if binding.path:
                for part in binding.path.split("."):
                    if isinstance(value, Sourced):
                        sources.extend(value.sources)
                    value = getattr(value, part)
                if isinstance(value, Sourced):
                    sources.extend(value.sources)
            if not sources:
                sources.extend(self._lineage.get(binding.node, []))
            return list(dict.fromkeys(sources))
        if isinstance(binding, Sourced):
            return list(binding.sources)
        if isinstance(binding, (list, tuple)):
            return list(
                dict.fromkeys(source for item in binding for source in self._requirement_sources(item))
            )
        return []

    def _cache_path(self, key: str) -> Path | None:
        return self._cache_dir / f"{key}.pickle" if self._cache_dir is not None else None

    def _load_cache(self, key: str) -> _CachedResult | None:
        if key in self._memory_cache:
            return self._memory_cache[key]
        path = self._cache_path(key)
        if path is None or not path.is_file():
            return None
        try:
            with path.open("rb") as cache_file:
                value = pickle.load(cache_file)
            if isinstance(value, _CachedResult):
                self._memory_cache[key] = value
                return value
        except Exception:
            return None
        return None

    def _store_cache(self, key: str, value: _CachedResult) -> None:
        try:
            pickle.dumps(value)
        except Exception:
            return
        self._memory_cache[key] = value
        path = self._cache_path(key)
        if path is None:
            return
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as cache_file:
                pickle.dump(value, cache_file, protocol=pickle.HIGHEST_PROTOCOL)
                temporary = Path(cache_file.name)
            temporary.replace(path)
        except Exception:
            try:
                temporary.unlink(missing_ok=True)
            except (UnboundLocalError, OSError):
                pass

    def set(self, node_name: str, **bindings: Any) -> Graph:
        """Change node bindings and invalidate cached evaluation state downstream."""

        if node_name not in self._nodes:
            raise GraphValidationError(f"unknown node {node_name!r}")
        node = self._nodes[node_name]
        previous = dict(node.bindings)
        node.bindings.update(bindings)
        try:
            self.check()
        except GraphValidationError:
            node.bindings = previous
            raise
        affected = {node_name}
        changed = True
        while changed:
            changed = False
            for name in self._nodes:
                if name not in affected and set(self._dependencies(name)) & affected:
                    affected.add(name)
                    changed = True
        for name in affected:
            self._values.pop(name, None)
            self._reasons.pop(name, None)
            self._lineage.pop(name, None)
            self._provenance.pop(name, None)
        return self

    def provenance(self, name: str) -> ProvenanceRecord:
        """Return the latest provenance record for a node."""

        if name not in self._provenance:
            raise GraphError(f"node {name!r} has not been evaluated")
        return self._provenance[name]

    def assumptions(self) -> dict[str, list[str]]:
        """Return each directly introduced provisional reason and its source nodes."""

        introduced: dict[str, list[str]] = defaultdict(list)
        for name, record in self._provenance.items():
            for reason in record.introduced_reasons:
                introduced[reason].append(name)
            for binding in self._nodes[name].bindings.values():
                for provisional in _provisionals_in(binding):
                    introduced[provisional.reason].append(name)
        return {reason: list(dict.fromkeys(nodes)) for reason, nodes in introduced.items()}

    def requirement_lineage(self, requirement_id: str) -> list[str]:
        """Return evaluated nodes reached through bindings carrying a requirement source."""

        return [name for name in self._nodes if requirement_id in self._lineage.get(name, [])]

    def to_dict(self) -> dict[str, Any]:
        """Serialize graph structure, provenance, and evaluated value previews."""

        nodes: dict[str, Any] = {}
        for name, node in self._nodes.items():
            nodes[name] = {
                "function": f"{node.function.__module__}.{node.function.__qualname__}",
                "version": node.metadata.version,
                "source": node.metadata.source,
                "stage": node.stage,
                "input_types": {
                    key: _type_name(node.hints.get(key, Any)) for key in node.signature.parameters
                },
                "output_type": _type_name(node.return_type),
                "bindings": {key: _encode_binding(value) for key, value in node.bindings.items()},
                "provenance": self._provenance[name].model_dump(mode="json")
                if name in self._provenance
                else None,
                "requirement_sources": self._lineage.get(name, []),
                "value_preview": _preview(self._values[name]) if name in self._values else None,
            }
        return {"format_version": 1, "nodes": nodes, "assumptions": self.assumptions()}

    def to_json(self, path: str | Path | None = None) -> str:
        """Return stable, human-readable JSON and optionally write it to disk."""

        content = json.dumps(self.to_dict(), indent=2, sort_keys=True)
        if path is not None:
            Path(path).write_text(content + "\n", encoding="utf-8")
        return content


def _refs_in(value: Any) -> list[str]:
    if isinstance(value, Ref):
        return [value.node]
    if isinstance(value, (list, tuple)):
        return [name for item in value for name in _refs_in(item)]
    return []


def _provisionals_in(value: Any) -> list[Provisional]:
    if isinstance(value, Provisional):
        return [value]
    if isinstance(value, (list, tuple)):
        return [entry for item in value for entry in _provisionals_in(item)]
    return []


def _sources_in(value: Any) -> list[str]:
    if isinstance(value, Sourced):
        return list(dict.fromkeys(value.sources + _sources_in(value.value)))
    if isinstance(value, BaseModel):
        return list(
            dict.fromkeys(
                source
                for field_name in type(value).model_fields
                for source in _sources_in(getattr(value, field_name))
            )
        )
    if isinstance(value, Mapping):
        return list(dict.fromkeys(source for item in value.values() for source in _sources_in(item)))
    if isinstance(value, (list, tuple)):
        return list(dict.fromkeys(source for item in value for source in _sources_in(item)))
    return []


def _unwrap_annotation(annotation: Any) -> Any:
    origin = get_origin(annotation)
    if origin in (Union, getattr(__import__("types"), "UnionType")):
        choices = [choice for choice in get_args(annotation) if choice is not type(None)]
        return choices[0] if len(choices) == 1 else annotation
    return annotation


def _sequence_element_type(annotation: Any) -> Any:
    args = get_args(annotation)
    if not args:
        return Any
    return args[0]


def _types_compatible(produced: Any, expected: Any) -> bool:
    if expected is Any or produced is Any:
        return True
    expected = _unwrap_annotation(expected)
    produced = _unwrap_annotation(produced)
    if expected == produced:
        return True
    expected_origin = get_origin(expected)
    produced_origin = get_origin(produced)
    if expected_origin in (Union, getattr(__import__("types"), "UnionType")):
        return any(_types_compatible(produced, choice) for choice in get_args(expected))
    if produced_origin in (Union, getattr(__import__("types"), "UnionType")):
        return all(_types_compatible(choice, expected) for choice in get_args(produced))
    if expected_origin is not None or produced_origin is not None:
        return expected_origin == produced_origin and get_args(expected) == get_args(produced)
    try:
        return inspect.isclass(produced) and inspect.isclass(expected) and issubclass(produced, expected)
    except TypeError:
        return False


def _value_matches_type(value: Any, expected: Any) -> bool:
    if expected is Any:
        return True
    expected = _unwrap_annotation(expected)
    origin = get_origin(expected)
    if origin in (Union, getattr(__import__("types"), "UnionType")):
        return any(_value_matches_type(value, choice) for choice in get_args(expected))
    if origin in (list, Sequence):
        return isinstance(value, (list, tuple)) and all(
            _value_matches_type(item, _sequence_element_type(expected)) for item in value
        )
    if expected is float and isinstance(value, int):
        return True
    try:
        return isinstance(value, expected)
    except TypeError:
        return True


def _type_name(value: Any) -> str:
    if value is Any:
        return "Any"
    return getattr(value, "__name__", str(value).replace("typing.", ""))


def _canonical(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return {"__model__": _type_name(type(value)), "fields": _canonical(value.model_dump(mode="python"))}
    if isinstance(value, Mapping):
        return {str(key): _canonical(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if type(value).__module__.startswith("numpy") and hasattr(value, "tobytes"):
        return {
            "__numpy__": True,
            "dtype": str(value.dtype),
            "shape": list(value.shape),
            "bytes": hashlib.sha256(value.tobytes()).hexdigest(),
        }
    if hasattr(value, "value") and hasattr(type(value), "__members__"):
        return {"__enum__": _type_name(type(value)), "value": _canonical(value.value)}
    try:
        return {"__pickle__": hashlib.sha256(pickle.dumps(value, protocol=5)).hexdigest()}
    except Exception as error:
        raise GraphError(f"cannot create a stable cache key for {_type_name(type(value))}") from error


def _digest(value: Any) -> str:
    encoded = json.dumps(_canonical(value), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _encode_binding(value: Any) -> Any:
    if isinstance(value, Ref):
        return {"ref": value.node, "path": value.path}
    if isinstance(value, Provisional):
        return {"provisional": _preview(value.value), "reason": value.reason}
    if isinstance(value, list):
        return [_encode_binding(item) for item in value]
    if isinstance(value, tuple):
        return {"tuple": [_encode_binding(item) for item in value]}
    return _preview(value)


def _preview(value: Any) -> Any:
    try:
        preview = _canonical(value)
        json.dumps(preview, allow_nan=False)
    except Exception:
        return {"type": _type_name(type(value)), "preview": repr(value)[:240]}
    text = json.dumps(preview, sort_keys=True)
    return preview if len(text) <= 500 else {"preview": text[:497] + "..."}