from __future__ import annotations

from dataclasses import dataclass
from typing import Final


class StatusContext:
    """Defined status-update context values."""
    PROFILE: Final[str] = "profile"
    STEP: Final[str] = "step"


class StatusCode:
    """Defined status-update state values."""
    STARTED: Final[str] = "started"
    DONE: Final[str] = "done"
    SKIPPED: Final[str] = "skipped"
    ERROR: Final[str] = "error"

@dataclass(frozen=True)
class StatusMessage:
    context_type: str
    current_task: int
    total: int
    status_code: str

    @classmethod
    def profile(cls, current_task: int, total: int, status_code: str) -> "StatusMessage":
        return cls(
            context_type=StatusContext.PROFILE,
            current_task=current_task,
            total=total,
            status_code=status_code,
        )

    @classmethod
    def step(cls, current_task: int, total: int, status_code: str) -> "StatusMessage":
        return cls(
            context_type=StatusContext.STEP,
            current_task=current_task,
            total=total,
            status_code=status_code,
        )