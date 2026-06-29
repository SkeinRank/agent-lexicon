"""JSONL evaluation datasets for Agent Lexicon behavior checks.

The dataset format is intentionally small and deterministic. It describes input
queries, expected terminology resolution, and optional tool-call safety
expectations. Metrics are implemented by a later layer; this module only loads
and validates dataset records.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from agent_lexicon.core import ResolutionAction, ResolutionStatus, ToolGuardAction, ToolGuardStatus


class EvalDatasetError(ValueError):
    """Raised when a queries.jsonl dataset cannot be loaded or validated."""


def _clean_text(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise EvalDatasetError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise EvalDatasetError(f"{field_name} must not be empty")
    return cleaned


def _clean_optional_text(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _clean_text(value, field_name=field_name)


def _clean_string_tuple(value: Any, *, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise EvalDatasetError(f"{field_name} must be a list of strings")
    return tuple(_clean_text(item, field_name=f"{field_name} item") for item in value)


def _clean_mapping(value: Any, *, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise EvalDatasetError(f"{field_name} must be a mapping")
    return {str(key): item for key, item in value.items()}


def _enum_or_none(enum_type: type[ResolutionStatus] | type[ResolutionAction] | type[ToolGuardStatus] | type[ToolGuardAction], value: Any, *, field_name: str):
    if value is None:
        return None
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(_clean_text(value, field_name=field_name))
    except ValueError as exc:
        allowed = ", ".join(item.value for item in enum_type)
        raise EvalDatasetError(f"{field_name} must be one of: {allowed}") from exc


@dataclass(frozen=True, slots=True)
class EvalToolCallExpectation:
    """Expected safety decision for a tool call in an eval query."""

    tool_name: str
    expected_status: ToolGuardStatus | None = None
    expected_action: ToolGuardAction | None = None
    expected_allowed: bool | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "tool_name", _clean_text(self.tool_name, field_name="tool call tool_name"))
        object.__setattr__(self, "expected_status", _enum_or_none(ToolGuardStatus, self.expected_status, field_name="tool call expected_status"))
        object.__setattr__(self, "expected_action", _enum_or_none(ToolGuardAction, self.expected_action, field_name="tool call expected_action"))
        if self.expected_allowed is not None and not isinstance(self.expected_allowed, bool):
            raise EvalDatasetError("tool call expected_allowed must be a boolean")
        object.__setattr__(self, "metadata", _clean_mapping(self.metadata, field_name="tool call metadata"))

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "tool_name": self.tool_name,
            "expected_status": self.expected_status.value if self.expected_status is not None else None,
            "expected_action": self.expected_action.value if self.expected_action is not None else None,
            "expected_allowed": self.expected_allowed,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class EvalQuery:
    """One query row from a local Agent Lexicon JSONL eval dataset."""

    id: str
    text: str
    scopes: tuple[str, ...] = ()
    expected_status: ResolutionStatus | None = None
    expected_action: ResolutionAction | None = None
    expected_term_ids: tuple[str, ...] = ()
    expected_primary_term_id: str | None = None
    tool_calls: tuple[EvalToolCallExpectation, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _clean_text(self.id, field_name="eval query id"))
        object.__setattr__(self, "text", _clean_text(self.text, field_name="eval query text"))
        object.__setattr__(self, "scopes", _clean_string_tuple(list(self.scopes), field_name="eval query scopes"))
        object.__setattr__(self, "expected_status", _enum_or_none(ResolutionStatus, self.expected_status, field_name="eval query expected_status"))
        object.__setattr__(self, "expected_action", _enum_or_none(ResolutionAction, self.expected_action, field_name="eval query expected_action"))
        object.__setattr__(self, "expected_term_ids", _clean_string_tuple(list(self.expected_term_ids), field_name="eval query expected_term_ids"))
        object.__setattr__(self, "expected_primary_term_id", _clean_optional_text(self.expected_primary_term_id, field_name="eval query expected_primary_term_id"))
        if self.expected_primary_term_id is not None and self.expected_term_ids and self.expected_primary_term_id not in self.expected_term_ids:
            raise EvalDatasetError("eval query expected_primary_term_id must be included in expected_term_ids")
        if not isinstance(self.tool_calls, tuple):
            object.__setattr__(self, "tool_calls", tuple(self.tool_calls))
        for tool_call in self.tool_calls:
            if not isinstance(tool_call, EvalToolCallExpectation):
                raise EvalDatasetError("eval query tool_calls must contain EvalToolCallExpectation objects")
        object.__setattr__(self, "metadata", _clean_mapping(self.metadata, field_name="eval query metadata"))

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "id": self.id,
            "text": self.text,
            "scopes": list(self.scopes),
            "expected_status": self.expected_status.value if self.expected_status is not None else None,
            "expected_action": self.expected_action.value if self.expected_action is not None else None,
            "expected_term_ids": list(self.expected_term_ids),
            "expected_primary_term_id": self.expected_primary_term_id,
            "tool_calls": [tool_call.to_dict() for tool_call in self.tool_calls],
            "metadata": dict(self.metadata),
        }


def load_eval_queries(path: str | Path) -> tuple[EvalQuery, ...]:
    """Load a JSONL eval dataset from disk."""
    source_path = Path(path)
    if not source_path.exists():
        raise EvalDatasetError(f"eval dataset file does not exist: {source_path}")
    if not source_path.is_file():
        raise EvalDatasetError(f"eval dataset path is not a file: {source_path}")
    return loads_eval_queries(source_path.read_text(encoding="utf-8"), source_path=source_path)


def loads_eval_queries(text: str, *, source_path: str | Path | None = None) -> tuple[EvalQuery, ...]:
    """Load a JSONL eval dataset from a string."""
    queries: list[EvalQuery] = []
    seen_ids: set[str] = set()
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            location = _line_location(source_path, line_number)
            raise EvalDatasetError(f"invalid JSON at {location}: {exc.msg}") from exc
        query = eval_query_from_dict(payload, line_number=line_number, source_path=source_path)
        if query.id in seen_ids:
            location = _line_location(source_path, line_number)
            raise EvalDatasetError(f"duplicate eval query id at {location}: {query.id}")
        seen_ids.add(query.id)
        queries.append(query)
    return tuple(queries)


def eval_query_from_dict(payload: Mapping[str, Any], *, line_number: int | None = None, source_path: str | Path | None = None) -> EvalQuery:
    """Build a validated eval query from a mapping."""
    if not isinstance(payload, Mapping):
        location = _line_location(source_path, line_number)
        raise EvalDatasetError(f"eval query at {location} must be a mapping")
    try:
        tool_calls = tuple(
            _tool_call_from_dict(tool_call_payload, line_number=line_number, index=index, source_path=source_path)
            for index, tool_call_payload in enumerate(_list(payload.get("tool_calls", []), field_name="tool_calls"))
        )
        return EvalQuery(
            id=_required(payload, "id"),
            text=_required(payload, "text"),
            scopes=_clean_string_tuple(payload.get("scopes", []), field_name="scopes"),
            expected_status=payload.get("expected_status"),
            expected_action=payload.get("expected_action"),
            expected_term_ids=_clean_string_tuple(payload.get("expected_term_ids", []), field_name="expected_term_ids"),
            expected_primary_term_id=payload.get("expected_primary_term_id"),
            tool_calls=tool_calls,
            metadata=_clean_mapping(payload.get("metadata", {}), field_name="metadata"),
        )
    except EvalDatasetError as exc:
        location = _line_location(source_path, line_number)
        raise EvalDatasetError(f"invalid eval query at {location}: {exc}") from exc


def _tool_call_from_dict(payload: Any, *, line_number: int | None, index: int, source_path: str | Path | None) -> EvalToolCallExpectation:
    if not isinstance(payload, Mapping):
        location = _line_location(source_path, line_number)
        raise EvalDatasetError(f"tool_calls[{index}] at {location} must be a mapping")
    return EvalToolCallExpectation(
        tool_name=_required(payload, "tool_name"),
        expected_status=payload.get("expected_status"),
        expected_action=payload.get("expected_action"),
        expected_allowed=payload.get("expected_allowed"),
        metadata=_clean_mapping(payload.get("metadata", {}), field_name=f"tool_calls[{index}].metadata"),
    )


def _required(payload: Mapping[str, Any], key: str) -> Any:
    if key not in payload:
        raise EvalDatasetError(f"missing required field: {key}")
    return payload[key]


def _list(value: Any, *, field_name: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise EvalDatasetError(f"{field_name} must be a list")
    return value


def _line_location(source_path: str | Path | None, line_number: int | None) -> str:
    if source_path is None and line_number is None:
        return "<memory>"
    if source_path is None:
        return f"line {line_number}"
    if line_number is None:
        return str(source_path)
    return f"{source_path}:{line_number}"


__all__ = [
    "EvalDatasetError",
    "EvalQuery",
    "EvalToolCallExpectation",
    "eval_query_from_dict",
    "load_eval_queries",
    "loads_eval_queries",
]
