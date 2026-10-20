# SPDX-License-Identifier: Apache-2.0
"""Connector base: stage inputs, run a tool as a separate process, read its outputs.

Every connector runs its tool as an external executable through `run_solver`, so no tool is
linked into this package (several are GPL-licensed). A run is reused when its rendered inputs,
parameters and command are unchanged.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from design_graph.engineering.solver import run_solver
from design_graph.model.elements import Evidence, Provenance, ToolDeclaration
from design_graph.model.system import SystemModel, basis_hash
from design_graph.model.units import Quantity

Category = Literal["cad-kernel", "cad", "meshing", "fea", "cfd", "multiphysics", "ecad"]
Parameter = Quantity | float

_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*([^{}]+?))?\s*\}\}")
_MANIFEST = ".dg-inputs.json"
_MARKER = ".dg-run.json"
_STATE = ".dg-connector.json"


class ConnectorError(RuntimeError):
    """Raised when a connector cannot stage, run, or read a tool."""


@dataclass(frozen=True)
class Reading:
    """One output value; `unit=None` means the tool does not say and none was declared."""

    value: float
    unit: str | None = None


class ToolInfo(BaseModel):
    connector: str
    executable: str
    version: str | None = None


class ConnectorRun(BaseModel):
    """What one run produced, with what is needed to turn an output into evidence."""

    connector: str
    tool_version: str | None
    command: list[str]
    input_hash: str
    reused: bool
    wall_time_s: float
    workdir: str
    outputs: dict[str, Quantity] = Field(default_factory=dict)
    undeclared: list[str] = Field(default_factory=list)


class Connector:
    """A tool run as a separate process. Subclasses define the command and the output reader."""

    id: ClassVar[str]
    name: ClassVar[str]
    category: ClassVar[Category]
    license: ClassVar[str]
    homepage: ClassVar[str]
    executables: ClassVar[tuple[str, ...]]
    version_args: ClassVar[tuple[str, ...]] = ("--version",)
    version_pattern: ClassVar[str] = r"(\d+(?:\.\d+)+)"
    deck_kind: ClassVar[Literal["file", "directory"]] = "file"
    inputs: ClassVar[str]
    outputs: ClassVar[str]

    def __init__(
        self,
        executable: str | None = None,
        *,
        units: dict[str, str] | None = None,
        options: dict[str, str] | None = None,
    ) -> None:
        self.executable = executable
        self.units = dict(units or {})
        self.options = dict(options or {})

    # ------------------------------------------------------------------ discovery

    @property
    def env_var(self) -> str:
        return f"DG_{self.id.upper()}_EXE"

    def resolve(self) -> str | None:
        """The executable to run: the explicit one, then $DG_<ID>_EXE, then the PATH."""

        for candidate in (self.executable, os.environ.get(self.env_var)):
            if candidate:
                return shutil.which(candidate) or (candidate if Path(candidate).is_file() else None)
        for name in self.executables:
            found = shutil.which(name)
            if found:
                return found
        return None

    def detect(self) -> ToolInfo | None:
        """The installed tool and, where it reports one, its version; None when not found."""

        executable = self.resolve()
        if executable is None:
            return None
        return ToolInfo(connector=self.id, executable=executable, version=self._probe(executable))

    def _probe(self, executable: str) -> str | None:
        if not self.version_args:
            return None
        try:
            done = subprocess.run(
                [executable, *self.version_args],
                capture_output=True,
                text=True,
                timeout=20,
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return self.version_from_text(done.stdout + "\n" + done.stderr)

    def version_from_text(self, text: str) -> str | None:
        match = re.search(self.version_pattern, text)
        return match.group(1) if match else None

    # ------------------------------------------------------------------ subclass hooks

    def command(self, executable: str, deck: Path, workdir: Path) -> list[str]:
        raise NotImplementedError

    def collect(self, workdir: Path, deck: Path) -> dict[str, Reading]:
        raise NotImplementedError

    def unit_of(self, kind: str, power: int = 1) -> str | None:
        """The declared unit for a physical kind (`length`, `stress`, ...), raised to `power`."""

        unit = self.units.get(kind)
        if unit is None:
            return None
        return unit if power == 1 else f"({unit})**{power}"

    # ------------------------------------------------------------------ run

    def run(
        self,
        deck: str | Path,
        workdir: str | Path,
        *,
        parameters: dict[str, Parameter] | None = None,
        support: list[str | Path] | None = None,
        timeout_s: float = 3600.0,
    ) -> ConnectorRun:
        """Stage `deck` (and `support` files) into `workdir`, render placeholders, run, collect.

        A placeholder is `{{name}}` for a dimensionless parameter or `{{name:unit}}`, which
        writes the parameter converted to that unit. Every placeholder needs a parameter and
        every parameter must be used.
        """

        executable = self.resolve()
        if executable is None:
            raise ConnectorError(
                f"{self.name} was not found: install it, put it on PATH, or set {self.env_var}"
            )
        source = Path(deck)
        if self.deck_kind == "directory" and not source.is_dir():
            raise ConnectorError(f"{self.name} needs a case directory, got {source}")
        if self.deck_kind == "file" and not source.is_file():
            raise ConnectorError(f"{self.name} needs an input file, got {source}")
        work = Path(workdir).resolve()
        work.mkdir(parents=True, exist_ok=True)
        params = dict(parameters or {})
        target, hashes = stage_inputs(source, work, params, [Path(s) for s in support or []])
        command = self.command(executable, target, work)
        manifest = work / _MANIFEST
        manifest.write_text(
            json.dumps(
                {
                    "connector": self.id,
                    "command": [Path(command[0]).name, *command[1:]],
                    "files": hashes,
                    "parameters": {k: _param_record(v) for k, v in sorted(params.items())},
                },
                sort_keys=True,
                indent=1,
            ),
            encoding="utf-8",
        )
        try:
            result = run_solver(manifest, command, timeout_s=timeout_s, marker_path=work / _MARKER)
        except RuntimeError as error:
            raise ConnectorError(f"{self.name}: {error}") from error
        state = work / _STATE
        if result.reused and state.is_file():
            version = json.loads(state.read_text(encoding="utf-8")).get("tool_version")
        else:
            version = self._probe(executable) or self.version_from_run(
                work, result.stdout + "\n" + result.stderr
            )
            state.write_text(json.dumps({"tool_version": version}), encoding="utf-8")
        outputs: dict[str, Quantity] = {}
        undeclared: list[str] = []
        for name, reading in sorted(self.collect(work, target).items()):
            unit = self.units.get(name, reading.unit)
            if unit is None:
                undeclared.append(name)
            outputs[name] = Quantity(reading.value, unit or "")
        if not outputs:
            raise ConnectorError(f"{self.name} produced no outputs in {work}")
        return ConnectorRun(
            connector=self.id,
            tool_version=version,
            command=result.command,
            input_hash=result.deck_hash,
            reused=result.reused,
            wall_time_s=result.wall_time_s,
            workdir=str(work),
            outputs=outputs,
            undeclared=undeclared,
        )

    def version_from_run(self, workdir: Path, output: str) -> str | None:
        """Version reported during the run, for tools without a usable version flag."""

        return self.version_from_text(output) if not self.version_args else None


# ---------------------------------------------------------------------- staging


def render(text: str, parameters: dict[str, Parameter], used: set[str]) -> str:
    """Replace `{{name}}` / `{{name:unit}}` placeholders; raise on unknown names or units."""

    def substitute(match: re.Match[str]) -> str:
        name, unit = match.group(1), match.group(2)
        if name not in parameters:
            raise ConnectorError(f"placeholder {{{{{name}}}}} has no parameter")
        used.add(name)
        value = parameters[name]
        if isinstance(value, Quantity):
            if unit is None:
                if value.unit:
                    raise ConnectorError(
                        f"placeholder {{{{{name}}}}} needs a unit, e.g. {{{{{name}:{value.unit}}}}}"
                    )
                number = value.value
            else:
                try:
                    number = value.to(unit.strip())
                except ValueError as error:
                    raise ConnectorError(f"placeholder {name}: {error}") from error
        else:
            if unit is not None:
                raise ConnectorError(f"parameter {name} is a bare number; give it a unit")
            number = float(value)
        return f"{number:.12g}"

    return _PLACEHOLDER.sub(substitute, text)


def stage_inputs(
    source: Path, workdir: Path, parameters: dict[str, Parameter], support: list[Path]
) -> tuple[Path, dict[str, str]]:
    """Copy the deck (a file or a case directory) and support files, rendering placeholders."""

    pairs: list[tuple[Path, Path]] = []
    if source.is_dir():
        for path in sorted(source.rglob("*")):
            relative = path.relative_to(source)
            if path.is_file() and not any(p.startswith(".dg-") for p in relative.parts):
                pairs.append((path, relative))
        target = workdir
    else:
        pairs.append((source, Path(source.name)))
        target = workdir / source.name
    for path in support:
        if not path.is_file():
            raise ConnectorError(f"support file does not exist: {path}")
        pairs.append((path, Path(path.name)))
    used: set[str] = set()
    hashes: dict[str, str] = {}
    for path, relative in pairs:
        data = path.read_bytes()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = None
        if text is not None and _PLACEHOLDER.search(text):
            data = render(text, parameters, used).encode("utf-8")
        destination = workdir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.is_file() or destination.read_bytes() != data:
            destination.write_bytes(data)
        hashes[relative.as_posix()] = hashlib.sha256(data).hexdigest()
    unused = sorted(set(parameters) - used)
    if unused:
        raise ConnectorError(f"parameters not used by any placeholder: {', '.join(unused)}")
    return target, hashes


def _param_record(value: Parameter) -> list[float | str]:
    if isinstance(value, Quantity):
        return [value.value, value.unit]
    return [float(value), ""]


# ---------------------------------------------------------------------- model bridge


def parameters_from_part(
    model: SystemModel, part: str, names: list[str] | None = None
) -> dict[str, Parameter]:
    """A part's attributes as connector parameters, so the deck is driven by the model."""

    item = next((p for p in model.parts if p.id == part), None)
    if item is None:
        raise ConnectorError(f"unknown part {part}")
    wanted = names if names is not None else sorted(item.attributes)
    missing = [n for n in wanted if n not in item.attributes]
    if missing:
        raise ConnectorError(f"part {part} has no attribute {', '.join(missing)}")
    return {n: item.attributes[n] for n in wanted}


def declare_tool(
    connector: Connector, *, id: str, declared_by: str, intended_use: str
) -> ToolDeclaration:
    """A tool declaration for the installed tool; the engineer named in `declared_by` owns it."""

    info = connector.detect()
    if info is None or info.version is None:
        raise ConnectorError(f"cannot read the installed {connector.name} version")
    return ToolDeclaration(
        id=id,
        name=connector.name,
        version=info.version,
        intended_use=intended_use,
        declared_by=declared_by,
    )


def evidence_from_run(
    model: SystemModel,
    run: ConnectorRun,
    *,
    output: str,
    id: str,
    case: str,
    requirement: str,
    created_by: str,
    date: str,
    uncertainty: Quantity | None = None,
) -> Evidence:
    """Turn one connector output into evidence bound to the current basis of (requirement, case).

    Refuses an output whose unit was never declared, and a run whose tool version differs from
    the version the case's tool declaration names.
    """

    if output not in run.outputs:
        raise ConnectorError(
            f"{run.connector} has no output {output!r}; outputs: {', '.join(sorted(run.outputs))}"
        )
    if output in run.undeclared:
        raise ConnectorError(
            f"{run.connector} output {output!r} has no unit; declare one with units={{...}}"
        )
    item = next((c for c in model.cases if c.id == case), None)
    if item is None:
        raise ConnectorError(f"unknown verification case {case}")
    tool = next((t for t in model.tools if t.id == item.tool), None)
    if tool is not None and run.tool_version is not None and tool.version != run.tool_version:
        raise ConnectorError(
            f"case {case} declares {tool.id} version {tool.version}, "
            f"but the run used {run.tool_version}"
        )
    return Evidence(
        id=id,
        case=case,
        requirement=requirement,
        created_by=created_by,
        date=date,
        basis_hash=basis_hash(model, requirement, case),
        value=run.outputs[output],
        uncertainty=uncertainty,
        provenance=Provenance(
            node=f"{run.connector}:{output}",
            input_hash=run.input_hash,
            function=f"{run.connector} {run.tool_version or 'unknown version'}",
        ),
    )
