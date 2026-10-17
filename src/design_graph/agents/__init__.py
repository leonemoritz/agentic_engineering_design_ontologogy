# SPDX-License-Identifier: Apache-2.0
"""Agent roles and orchestration over the system model.

A manager turns the process layer's open items into tasks for expert roles; every accepted change is
reviewed by an independent quality role before it enters the model; approvals, attestations, tool
declarations and tests stay with people.
"""

from design_graph.agents.backends import CallableBackend, PromptBackend, model_digest
from design_graph.agents.orchestrator import (
    AgentBackend,
    OrchestrationReport,
    Orchestrator,
    Proposal,
    ReviewTask,
    Task,
    TaskRecord,
)
from design_graph.agents.roles import DEFAULT_ROLES, FORBIDDEN_FOR_AGENTS, RoleSpec

__all__ = [
    "DEFAULT_ROLES",
    "FORBIDDEN_FOR_AGENTS",
    "AgentBackend",
    "CallableBackend",
    "OrchestrationReport",
    "Orchestrator",
    "PromptBackend",
    "Proposal",
    "ReviewTask",
    "RoleSpec",
    "Task",
    "TaskRecord",
    "model_digest",
]
