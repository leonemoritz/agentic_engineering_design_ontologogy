# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest
from pydantic import BaseModel

from design_graph import (
    Graph,
    Lever,
    SizingController,
    SolverTimeout,
    rule,
    run_solver,
)


class Margin(BaseModel):
    value: float


@rule(version="1", source="invented strength check")
def calculate_margin(thickness_mm: float) -> Margin:
    return Margin(value=thickness_mm - 2.0)


def make_sizing_graph() -> Graph:
    return Graph().add("margin", calculate_margin, {"thickness_mm": 1.0})


def test_controller_keeps_only_improving_trials() -> None:
    graph = make_sizing_graph()
    action = SizingController(
        graph,
        target_node="margin",
        margin_path="value",
        lever=Lever(
            name="wall thickness",
            node="margin",
            binding="thickness_mm",
            step=-1.0,
            reach=5.0,
            reason="synthetic reverse lever",
        ),
    ).run()[0]
    assert action.outcome == "tried, reverted"
    assert graph.binding("margin", "thickness_mm") == 1.0
    assert graph.evaluate("margin")["margin"].value == -1.0


def test_controller_accepts_improvement_and_advises_beyond_reach() -> None:
    accepted = SizingController(
        make_sizing_graph(),
        target_node="margin",
        margin_path="value",
        lever=Lever(
            name="wall thickness",
            node="margin",
            binding="thickness_mm",
            step=2.0,
            reach=5.0,
            reason="close the synthetic gap",
        ),
    ).run()[0]
    assert accepted.outcome == "accepted"
    assert accepted.margin_after == 1.0

    far_graph = Graph().add("margin", calculate_margin, {"thickness_mm": -10.0})
    advice = SizingController(
        far_graph,
        target_node="margin",
        margin_path="value",
        lever=Lever(
            name="wall thickness",
            node="margin",
            binding="thickness_mm",
            step=1.0,
            reach=3.0,
            reason="test reach boundary",
        ),
    ).run()[0]
    assert advice.outcome == "advice only"
    assert far_graph.binding("margin", "thickness_mm") == -10.0


def test_solver_reuses_successful_deck_hash(tmp_path: Path) -> None:
    deck = tmp_path / "invented.inp"
    deck.write_text("synthetic solver input\n", encoding="utf-8")
    command = [sys.executable, "-c", "print('synthetic solve')", "{deck}"]
    first = run_solver(deck, command, timeout_s=5.0)
    second = run_solver(deck, command, timeout_s=5.0)
    assert first.reused is False
    assert first.stdout.strip() == "synthetic solve"
    assert second.reused is True
    assert second.deck_hash == first.deck_hash


def test_solver_timeout_terminates_process_group(tmp_path: Path) -> None:
    deck = tmp_path / "slow.inp"
    deck.write_text("synthetic slow input\n", encoding="utf-8")
    with pytest.raises(SolverTimeout, match="process group was terminated"):
        run_solver(
            deck,
            [sys.executable, "-c", "import time; time.sleep(10)", "{deck}"],
            timeout_s=0.05,
            terminate_grace_s=0.05,
        )


def test_solver_timeout_signals_grandchild_process(tmp_path: Path) -> None:
    deck = tmp_path / "tree.inp"
    deck.write_text("synthetic process-tree input\n", encoding="utf-8")
    child_marker = tmp_path / "child-terminated"
    child_pid_file = tmp_path / "child.pid"
    child_code = (
        "import os,signal,time; from pathlib import Path; "
        f"signal.signal(signal.SIGTERM,lambda *_: "
        f"(Path({str(child_marker)!r}).write_text('yes'),os._exit(0))); "
        "time.sleep(30)"
    )
    parent_code = (
        "import subprocess,sys,time; child=subprocess.Popen([sys.executable,'-c',sys.argv[1]]); "
        "open(sys.argv[2],'w').write(str(child.pid)); time.sleep(30)"
    )
    with pytest.raises(SolverTimeout):
        run_solver(
            deck,
            [sys.executable, "-c", parent_code, child_code, str(child_pid_file), "{deck}"],
            timeout_s=0.2,
            terminate_grace_s=1.0,
        )
    assert child_marker.read_text(encoding="utf-8") == "yes"


def test_solver_timeout_kills_grandchild_that_ignores_sigterm(tmp_path: Path) -> None:
    deck = tmp_path / "stubborn-tree.inp"
    deck.write_text("synthetic stubborn process-tree input\n", encoding="utf-8")
    child_pid_file = tmp_path / "stubborn-child.pid"
    child_code = "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(30)"
    parent_code = (
        "import signal,subprocess,sys,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
        "child=subprocess.Popen([sys.executable,'-c',sys.argv[1]]); "
        "open(sys.argv[2],'w').write(str(child.pid)); time.sleep(30)"
    )
    with pytest.raises(SolverTimeout):
        run_solver(
            deck,
            [sys.executable, "-c", parent_code, child_code, str(child_pid_file), "{deck}"],
            timeout_s=0.3,
            terminate_grace_s=0.05,
        )
    child_pid = int(child_pid_file.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)
    else:
        pytest.fail("grandchild process remained alive after process-group timeout")
