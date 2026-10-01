from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Mapping, TypeAlias


@dataclass(frozen=True, slots=True)
class BuildStart:
    target: str
    event: str = field(default="build_start", init=False)


@dataclass(frozen=True, slots=True)
class GroupStart:
    name: str
    index: int
    total: int
    profiles: tuple[str, ...]
    event: str = field(default="group_start", init=False)


@dataclass(frozen=True, slots=True)
class ProfileStart:
    name: str
    index: int
    total: int
    step_total: int
    step_run_total: int
    steps_to_run: tuple[str, ...]
    event: str = field(default="profile_start", init=False)


@dataclass(frozen=True, slots=True)
class StepsSkipped:
    names: tuple[str, ...]
    event: str = field(default="steps_skipped", init=False)


@dataclass(frozen=True, slots=True)
class StepStart:
    name: str
    index: int
    total: int
    status_text: str
    event: str = field(default="step_start", init=False)
    description: str | None = None


@dataclass(frozen=True, slots=True)
class StepProgress:
    name: str
    progress: float
    event: str = field(default="step_progress", init=False)


@dataclass(frozen=True, slots=True)
class StepFinish:
    name: str
    success: bool
    elapsed_s: float
    status_text: str
    event: str = field(default="step_finish", init=False)


@dataclass(frozen=True, slots=True)
class ProfileFinish:
    name: str
    success: bool
    elapsed_s: float
    status_text: str
    event: str = field(default="profile_finish", init=False)


@dataclass(frozen=True, slots=True)
class GroupFinish:
    name: str
    success: bool
    elapsed_s: float
    status_text: str
    event: str = field(default="group_finish", init=False)


@dataclass(frozen=True, slots=True)
class BuildFinish:
    success: bool
    elapsed_s: float
    status_text: str
    event: str = field(default="build_finish", init=False)


StatusMessage: TypeAlias = (
    BuildStart
    | GroupStart
    | ProfileStart
    | StepsSkipped
    | StepStart
    | StepProgress
    | StepFinish
    | ProfileFinish
    | GroupFinish
    | BuildFinish
)


_MESSAGE_TYPES: dict[str, type[StatusMessage]] = {
    "build_start": BuildStart,
    "group_start": GroupStart,
    "profile_start": ProfileStart,
    "steps_skipped": StepsSkipped,
    "step_start": StepStart,
    "step_progress": StepProgress,
    "step_finish": StepFinish,
    "profile_finish": ProfileFinish,
    "group_finish": GroupFinish,
    "build_finish": BuildFinish,
}


def to_dict(message: StatusMessage) -> dict[str, Any]:
    """Serialize a status message to a JSON-compatible dictionary."""
    return asdict(message)


def to_json(message: StatusMessage) -> str:
    """Serialize one status message as a compact NDJSON record body."""
    return json.dumps(to_dict(message), separators=(",", ":"))


def from_json(text: str) -> StatusMessage:
    """Decode one JSON status record."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid status JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError("Status JSON must decode to an object")

    return from_dict(payload)


def from_dict(payload: Mapping[str, Any]) -> StatusMessage:
    """Decode a status-message mapping into its concrete dataclass.

    The ``event`` field selects the message type. Every constructor field for
    that message is required; missing fields fail rather than receiving runtime
    defaults.
    """
    try:
        event = payload["event"]
    except KeyError as exc:
        raise ValueError("Missing required status field: 'event'") from exc

    if not isinstance(event, str):
        raise ValueError("Status field 'event' must be a string")

    try:
        message_type = _MESSAGE_TYPES[event]
    except KeyError as exc:
        raise ValueError(f"Unknown status event: {event!r}") from exc

    constructor_fields = tuple(
        data_field.name
        for data_field in fields(message_type)
        if data_field.init
    )

    try:
        values = {name: payload[name] for name in constructor_fields}
    except KeyError as exc:
        raise ValueError(
            f"Missing required field for {message_type.__name__}: {exc.args[0]!r}"
        ) from exc

    try:
        return message_type(**values)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Invalid {message_type.__name__} status message: {exc}"
        ) from exc
