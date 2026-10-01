from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, NamedTuple

import networkx as nx



class ReasonCode(Enum):
    """Reason LiteBuild selected a step for execution or considered it current."""

    UP_TO_DATE = (0, "Up to date")
    MISSING_OUTPUT = (1, "Creating output")
    COMMAND_CHANGED = (3, "Command changed")
    INPUTS_CHANGED = (4, "Input file list changed")
    PARAMS_CHANGED = (5, "Parameters changed")
    NEWER_INPUT = (6, "Input '{context}' is newer")
    MISSING_INPUT = (7, "Input '{context}' is missing")
    STALE_TARGET = (8, "Upstream dependency requires rebuild")
    FORCED = (9, "Forced rebuild")
    OUTPUT_CHANGED = (10, "Output path changed")

    @property
    def code(self) -> int:
        return self.value[0]

    @property
    def description(self) -> str:
        """Canonical human-readable reason template."""
        return self.value[1]

    def status_text(self, context: str = "") -> str:
        """Return the canonical human-readable reason for status/log output."""
        return self.description.format(context=context)


@dataclass(frozen=True, slots=True)
class BuildStep:
    """A resolved workflow step and LiteBuild's reason for its planned state."""

    node_name: str
    description: str
    command: Dict
    reason_code: ReasonCode
    context: str


class BuildPlan(NamedTuple):
    """The complete incremental execution plan for one profile."""

    steps_to_run: List[BuildStep]
    steps_to_skip: List[BuildStep]
    command_map: Dict
    execution_graph: nx.DiGraph
