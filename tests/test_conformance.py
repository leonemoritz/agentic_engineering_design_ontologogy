# SPDX-License-Identifier: Apache-2.0
"""Conformance vectors are current, and the independent checker reproduces them."""

from __future__ import annotations

import ast
import importlib.util
import json
import random
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONFORMANCE = ROOT / "conformance"
CHECKER = CONFORMANCE / "independent" / "check.py"
STDLIB = {"__future__", "hashlib", "json", "math", "sys", "pathlib", "typing"}


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(CHECKER), str(root)], capture_output=True, text=True, check=False
    )


def test_vectors_are_current() -> None:
    build = _load(ROOT / "scripts" / "build_conformance.py", "build_conformance")
    assert build.main(["--check"]) == 0, "run 'python scripts/build_conformance.py'"


def test_independent_checker_reproduces_every_vector() -> None:
    result = _run(CONFORMANCE)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "0 failing file(s)" in result.stdout


def test_checker_uses_only_the_standard_library() -> None:
    tree = ast.parse(CHECKER.read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".")[0] for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }  # fmt: skip
    assert imported <= STDLIB, imported - STDLIB


def _edit(root: Path, name: str, change: Any) -> None:
    path = next((root / "vectors").glob(f"{name}-*.json"))
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.parametrize(
    "name, change",
    [
        ("V01", lambda d: d["expected"]["assessment"][0].update(satisfaction="NOT_SATISFIED")),
        ("V01", lambda d: d["model"]["requirements"][0]["target"].update(value=3.1)),
        ("V02", lambda d: d["expected"]["assessment"][0].update(margin_si=-99.0)),
        ("V08", lambda d: d["model"]["evidence"][0].update(basis_hash="0" * 64)),
        ("V12", lambda d: d["model"]["principals"][1].update(kind="human", roles=["verifier"])),
    ],
)
def test_checker_detects_changes(tmp_path: Path, name: str, change: Any) -> None:
    root = tmp_path / "conformance"
    shutil.copytree(CONFORMANCE, root, ignore=shutil.ignore_patterns("independent"))
    _edit(root, name, change)
    result = _run(root)
    assert result.returncode == 1
    assert f"FAIL {name}" in result.stdout


def test_canonical_json_matches_reference_on_random_values() -> None:
    checker = _load(CHECKER, "independent_check")
    rng = random.Random(7)
    alphabet = 'aZ09 "\\/\t\n\x00\x1f\x7fä€\U0001f525'
    for _ in range(3000):
        x = rng.choice(
            [
                rng.uniform(-1e3, 1e3),
                rng.random() * 10.0 ** rng.randint(-30, 30),
                float(rng.randint(-(10**18), 10**18)),
                rng.randint(-(10**20), 10**20),
            ]
        )
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 6)))
        value = {text: [x, -x], "k": text}
        reference = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        assert checker.canonical(value) == reference
