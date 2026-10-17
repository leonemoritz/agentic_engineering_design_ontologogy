# SPDX-License-Identifier: Apache-2.0
"""External solver execution with deck-hash reuse and process-group timeouts."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from pydantic import BaseModel


class SolverError(RuntimeError):
    """Raised when an external solver fails."""


class SolverTimeout(SolverError):
    """Raised after the solver process tree has been terminated on timeout."""


class SolverResult(BaseModel):
    deck_hash: str
    command: list[str]
    reused: bool
    return_code: int
    wall_time_s: float
    stdout: str = ""
    stderr: str = ""


def run_solver(
    deck_path: str | Path,
    command: list[str],
    *,
    timeout_s: float,
    marker_path: str | Path | None = None,
    terminate_grace_s: float = 0.5,
) -> SolverResult:
    """Run a separate solver executable once per identical input-deck hash."""

    deck = Path(deck_path).resolve()
    if not deck.is_file():
        raise SolverError(f"solver input deck does not exist: {deck}")
    if not command:
        raise SolverError("solver command cannot be empty")
    if timeout_s <= 0:
        raise ValueError("solver timeout must be positive")
    digest = hashlib.sha256(deck.read_bytes()).hexdigest()
    marker = (
        Path(marker_path) if marker_path is not None else deck.with_suffix(deck.suffix + ".solved")
    )
    try:
        existing = json.loads(marker.read_text(encoding="utf-8"))
        if existing.get("deck_hash") == digest and existing.get("return_code") == 0:
            return SolverResult(
                deck_hash=digest,
                command=command,
                reused=True,
                return_code=0,
                wall_time_s=0.0,
            )
    except (OSError, json.JSONDecodeError, AttributeError):
        pass

    invocation = [argument.replace("{deck}", str(deck)) for argument in command]
    started = time.perf_counter()
    process = subprocess.Popen(
        invocation,
        cwd=deck.parent,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=(os.name == "posix"),
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired as error:
        _terminate_process_tree(process, terminate_grace_s)
        stdout, stderr = process.communicate()
        raise SolverTimeout(
            f"solver timed out after {timeout_s:g}s and its process group was terminated"
        ) from error
    elapsed = time.perf_counter() - started
    if process.returncode != 0:
        raise SolverError(
            f"solver exited with code {process.returncode}: {stderr.strip() or stdout.strip()}"
        )
    result = SolverResult(
        deck_hash=digest,
        command=invocation,
        reused=False,
        return_code=0,
        wall_time_s=elapsed,
        stdout=stdout,
        stderr=stderr,
    )
    marker.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=marker.parent, delete=False
    ) as handle:
        json.dump({"deck_hash": digest, "return_code": 0, "command": invocation}, handle)
        temporary = Path(handle.name)
    temporary.replace(marker)
    return result


def _terminate_process_tree(process: subprocess.Popen[str], grace_s: float) -> None:
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return
        try:
            process.wait(timeout=grace_s)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):  # macOS: EPERM once only zombies remain
            pass
        process.wait()
    else:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=grace_s)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
