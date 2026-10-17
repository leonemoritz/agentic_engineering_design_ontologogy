# SPDX-License-Identifier: Apache-2.0
"""Process layer: stage templates, completeness and consistency rules, stage status, next actions.

The system model says whether a design is verified. The process layer says what is missing, in which
stage it belongs, what to do about it, and who should do it.
"""

from design_graph.process.rules import HUMAN, RULES, STAGE_OWNER, OpenItem, RuleSpec, open_items
from design_graph.process.status import ProcessStatus, StageStatus, evaluate_process
from design_graph.process.templates import (
    DEFAULT_TEMPLATE,
    ProcessTemplate,
    StageTemplate,
    load_template,
)

__all__ = [
    "DEFAULT_TEMPLATE",
    "HUMAN",
    "RULES",
    "STAGE_OWNER",
    "OpenItem",
    "ProcessStatus",
    "ProcessTemplate",
    "RuleSpec",
    "StageStatus",
    "StageTemplate",
    "evaluate_process",
    "load_template",
    "open_items",
]
