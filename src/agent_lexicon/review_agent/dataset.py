"""Review dataset export and quality checks for local review loops.

This module turns local workspace review events into portable JSONL examples.
The examples are intended for future evals, regression tests, and optional model
improvement workflows. It does not train models and does not require external
services.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from agent_lexicon.review_agent.agent import ReviewAgentDecision, ReviewAgentError, run_review_agent
from agent_lexicon.workspace import WorkspaceReviewEvent, WorkspaceReviewItem, WorkspaceState


class ReviewDatasetError(ValueError):
    """Raised when a review dataset cannot be built or exported."""


class ReviewDatasetQuality(str, Enum):
    """Quality label for one exported review dataset example."""

    USABLE = "usable"
    INCOMPLETE = "incomplete"
    CONFLICTING = "conflicting"
    UNSAFE = "unsafe"
    LOW_SIGNAL = "low_signal"


@dataclass(frozen=True, slots=True)
class ReviewDatasetExample:
    """One portable review dataset example."""

    example_id: str
    surface: str
    normalized_surface: str
    human_decision: str
    reviewer: str
    review_note: str
    created_at: str
    quality: ReviewDatasetQuality
    quality_score: float
    quality_flags: tuple[str, ...] = ()
    candidate: Mapping[str, Any] = field(default_factory=dict)
    evidence: Mapping[str, Any] = field(default_factory=dict)
    review_agent_decision: ReviewAgentDecision | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "example_id", _clean_text(self.example_id, field_name="example_id"))
        object.__setattr__(self, "surface", _clean_text(self.surface, field_name="surface"))
        object.__setattr__(self, "normalized_surface", _clean_text(self.normalized_surface, field_name="normalized_surface"))
        object.__setattr__(self, "human_decision", _clean_text(self.human_decision, field_name="human_decision"))
        object.__setattr__(self, "reviewer", _clean_text(self.reviewer, field_name="reviewer"))
        object.__setattr__(self, "review_note", self.review_note.strip() if isinstance(self.review_note, str) else "")
        object.__setattr__(self, "created_at", _clean_text(self.created_at, field_name="created_at"))
        object.__setattr__(
            self,
            "quality",
            ReviewDatasetQuality(self.quality.value if isinstance(self.quality, ReviewDatasetQuality) else str(self.quality)),
        )
        object.__setattr__(self, "quality_score", _bounded_score(self.quality_score))
        if not isinstance(self.quality_flags, tuple):
            object.__setattr__(self, "quality_flags", tuple(str(flag).strip() for flag in self.quality_flags if str(flag).strip()))
        else:
            object.__setattr__(self, "quality_flags", tuple(str(flag).strip() for flag in self.quality_flags if str(flag).strip()))
        if not isinstance(self.candidate, Mapping):
            raise ReviewDatasetError("candidate must be a mapping")
        if not isinstance(self.evidence, Mapping):
            raise ReviewDatasetError("evidence must be a mapping")
        if self.review_agent_decision is not None and not isinstance(self.review_agent_decision, ReviewAgentDecision):
            raise ReviewDatasetError("review_agent_decision must be a ReviewAgentDecision")
        if not isinstance(self.metadata, Mapping):
            raise ReviewDatasetError("metadata must be a mapping")
        object.__setattr__(self, "candidate", dict(self.candidate))
        object.__setattr__(self, "evidence", dict(self.evidence))
        object.__setattr__(self, "metadata", {str(key): value for key, value in self.metadata.items()})

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dataset example."""
        return {
            "example_id": self.example_id,
            "surface": self.surface,
            "normalized_surface": self.normalized_surface,
            "human_decision": self.human_decision,
            "reviewer": self.reviewer,
            "review_note": self.review_note,
            "created_at": self.created_at,
            "quality": self.quality.value,
            "quality_score": self.quality_score,
            "quality_flags": list(self.quality_flags),
            "candidate": dict(self.candidate),
            "evidence": dict(self.evidence),
            "review_agent_decision": self.review_agent_decision.to_dict() if self.review_agent_decision else None,
            "metadata": dict(self.metadata),
        }

    def to_json_line(self) -> str:
        """Return the example as one stable JSONL row."""
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True, slots=True)
class ReviewDatasetReport:
    """Result returned by review dataset export."""

    examples: tuple[ReviewDatasetExample, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.examples, tuple):
            object.__setattr__(self, "examples", tuple(self.examples))
        for example in self.examples:
            if not isinstance(example, ReviewDatasetExample):
                raise ReviewDatasetError("examples must contain ReviewDatasetExample objects")
        if not isinstance(self.metadata, Mapping):
            raise ReviewDatasetError("metadata must be a mapping")
        object.__setattr__(self, "metadata", {str(key): value for key, value in self.metadata.items()})

    @property
    def example_count(self) -> int:
        """Return the number of exported examples."""
        return len(self.examples)

    @property
    def usable_count(self) -> int:
        """Return the number of examples marked usable."""
        return self.count_by_quality(ReviewDatasetQuality.USABLE)

    @property
    def unsafe_count(self) -> int:
        """Return the number of unsafe examples."""
        return self.count_by_quality(ReviewDatasetQuality.UNSAFE)

    def count_by_quality(self, quality: ReviewDatasetQuality | str) -> int:
        """Return the number of examples with a given quality label."""
        quality_value = ReviewDatasetQuality(quality.value if isinstance(quality, ReviewDatasetQuality) else str(quality))
        return sum(1 for example in self.examples if example.quality == quality_value)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dataset report."""
        quality_counts = {quality.value: self.count_by_quality(quality) for quality in ReviewDatasetQuality}
        return {
            "example_count": self.example_count,
            "usable_count": self.usable_count,
            "unsafe_count": self.unsafe_count,
            "quality_counts": quality_counts,
            "examples": [example.to_dict() for example in self.examples],
            "metadata": dict(self.metadata),
        }

    def to_jsonl(self) -> str:
        """Return all examples as JSONL."""
        return "".join(f"{example.to_json_line()}\n" for example in self.examples)


@dataclass(frozen=True, slots=True)
class ReviewDatasetQualitySummary:
    """Quality evaluation for one review event before export."""

    quality: ReviewDatasetQuality
    score: float
    flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "quality",
            ReviewDatasetQuality(self.quality.value if isinstance(self.quality, ReviewDatasetQuality) else str(self.quality)),
        )
        object.__setattr__(self, "score", _bounded_score(self.score))
        if not isinstance(self.flags, tuple):
            object.__setattr__(self, "flags", tuple(str(flag).strip() for flag in self.flags if str(flag).strip()))
        else:
            object.__setattr__(self, "flags", tuple(str(flag).strip() for flag in self.flags if str(flag).strip()))

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable quality summary."""
        return {"quality": self.quality.value, "score": self.score, "flags": list(self.flags)}


def build_review_dataset(
    state: WorkspaceState,
    *,
    include_review_agent: bool = False,
    quality: ReviewDatasetQuality | str | None = None,
    limit: int | None = None,
) -> ReviewDatasetReport:
    """Build a portable review dataset from local workspace review events."""
    if not isinstance(state, WorkspaceState):
        raise ReviewDatasetError("state must be a WorkspaceState")
    if limit is not None and limit < 1:
        raise ReviewDatasetError("limit must be greater than 0")
    quality_filter: ReviewDatasetQuality | None = None
    if quality is not None:
        quality_filter = ReviewDatasetQuality(quality.value if isinstance(quality, ReviewDatasetQuality) else str(quality))

    events = state.list_review_events(limit=limit)
    decision_sets = _decision_sets_by_surface(events)
    examples: list[ReviewDatasetExample] = []
    for event in events:
        item = state.get_review_item(event.normalized_surface)
        quality_summary = evaluate_review_event_quality(event, decision_sets=decision_sets)
        if quality_filter is not None and quality_summary.quality != quality_filter:
            continue
        review_agent_decision = None
        if include_review_agent and item is not None:
            try:
                review_agent_decision = run_review_agent(item)
            except ReviewAgentError:
                review_agent_decision = None
                quality_summary = _append_quality_flag(quality_summary, "review_agent_unavailable")
        examples.append(
            review_dataset_example_from_event(
                event,
                quality_summary=quality_summary,
                review_item=item,
                review_agent_decision=review_agent_decision,
            )
        )

    metadata = {
        "source": "local_workspace_review_events",
        "include_review_agent": include_review_agent,
        "quality_filter": quality_filter.value if quality_filter else None,
        "review_event_count": len(events),
    }
    return ReviewDatasetReport(examples=tuple(examples), metadata=metadata)


def export_review_dataset_jsonl(
    state: WorkspaceState,
    output_path: str | Path | None = None,
    *,
    include_review_agent: bool = False,
    quality: ReviewDatasetQuality | str | None = None,
    limit: int | None = None,
) -> str:
    """Export local review dataset examples as JSONL."""
    report = build_review_dataset(
        state,
        include_review_agent=include_review_agent,
        quality=quality,
        limit=limit,
    )
    content = report.to_jsonl()
    if output_path is not None:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return content


def evaluate_review_event_quality(
    event: WorkspaceReviewEvent,
    *,
    decision_sets: Mapping[str, frozenset[str]] | None = None,
) -> ReviewDatasetQualitySummary:
    """Classify one review event for future eval or SFT dataset use."""
    if not isinstance(event, WorkspaceReviewEvent):
        raise ReviewDatasetError("event must be a WorkspaceReviewEvent")
    candidate = dict(event.candidate_snapshot)
    evidence = dict(event.evidence_snapshot)
    flags: list[str] = []

    if _evidence_is_unsafe(evidence):
        flags.append("unsafe_prompt_evidence")
        return ReviewDatasetQualitySummary(ReviewDatasetQuality.UNSAFE, 0.0, tuple(flags))

    if decision_sets is not None and len(decision_sets.get(event.normalized_surface, frozenset())) > 1:
        flags.append("conflicting_human_decisions")
        return ReviewDatasetQualitySummary(ReviewDatasetQuality.CONFLICTING, 0.25, tuple(flags))

    if not candidate:
        flags.append("missing_candidate_snapshot")
    if not evidence:
        flags.append("missing_evidence_snapshot")
    if evidence and _positive_count(evidence) == 0:
        flags.append("missing_positive_evidence")
    if not event.note:
        flags.append("missing_review_note")

    if any(flag in flags for flag in ("missing_candidate_snapshot", "missing_evidence_snapshot", "missing_positive_evidence")):
        return ReviewDatasetQualitySummary(ReviewDatasetQuality.INCOMPLETE, 0.35, tuple(flags))

    score = _candidate_score(candidate)
    positive_count = _positive_count(evidence)
    if score < 0.3 or positive_count < 1:
        flags.append("low_signal_candidate")
        return ReviewDatasetQualitySummary(ReviewDatasetQuality.LOW_SIGNAL, 0.6, tuple(flags))

    if not flags:
        flags.append("review_example_complete")
    return ReviewDatasetQualitySummary(ReviewDatasetQuality.USABLE, 1.0 if flags == ["review_example_complete"] else 0.85, tuple(flags))


def review_dataset_example_from_event(
    event: WorkspaceReviewEvent,
    *,
    quality_summary: ReviewDatasetQualitySummary | None = None,
    review_item: WorkspaceReviewItem | None = None,
    review_agent_decision: ReviewAgentDecision | None = None,
) -> ReviewDatasetExample:
    """Create a dataset example from one workspace review event."""
    if not isinstance(event, WorkspaceReviewEvent):
        raise ReviewDatasetError("event must be a WorkspaceReviewEvent")
    if review_item is not None and not isinstance(review_item, WorkspaceReviewItem):
        raise ReviewDatasetError("review_item must be a WorkspaceReviewItem")
    if quality_summary is None:
        quality_summary = evaluate_review_event_quality(event)

    candidate = dict(event.candidate_snapshot)
    evidence = dict(event.evidence_snapshot)
    surface = str(candidate.get("surface") or evidence.get("surface") or event.normalized_surface)
    metadata = {
        "event_id": event.event_id,
        "event_type": event.event_type.value,
        "source": "workspace_review_event",
    }
    if review_item is not None:
        metadata["current_workspace_review_status"] = review_item.review_status

    return ReviewDatasetExample(
        example_id=f"review_example_{event.event_id}",
        surface=surface,
        normalized_surface=event.normalized_surface,
        human_decision=event.decision.value,
        reviewer=event.reviewer,
        review_note=event.note,
        created_at=event.created_at,
        quality=quality_summary.quality,
        quality_score=quality_summary.score,
        quality_flags=quality_summary.flags,
        candidate=candidate,
        evidence=evidence,
        review_agent_decision=review_agent_decision,
        metadata=metadata,
    )


def _decision_sets_by_surface(events: tuple[WorkspaceReviewEvent, ...]) -> dict[str, frozenset[str]]:
    decisions: dict[str, set[str]] = {}
    for event in events:
        decisions.setdefault(event.normalized_surface, set()).add(event.decision.value)
    return {surface: frozenset(values) for surface, values in decisions.items()}


def _append_quality_flag(summary: ReviewDatasetQualitySummary, flag: str) -> ReviewDatasetQualitySummary:
    flags = tuple((*summary.flags, flag)) if flag not in summary.flags else summary.flags
    return ReviewDatasetQualitySummary(summary.quality, summary.score, flags)


def _evidence_is_unsafe(evidence: Mapping[str, Any]) -> bool:
    metadata = evidence.get("metadata", {}) if isinstance(evidence, Mapping) else {}
    safety = metadata.get("prompt_safety", {}) if isinstance(metadata, Mapping) else {}
    if not isinstance(safety, Mapping):
        return False
    if str(safety.get("action", "")) == "block_llm_review":
        return True
    if str(safety.get("highest_risk", "")) == "high":
        return True
    return int(safety.get("high_count", 0) or 0) > 0


def _positive_count(evidence: Mapping[str, Any]) -> int:
    if not isinstance(evidence, Mapping):
        return 0
    if "positive_count" in evidence:
        return int(evidence.get("positive_count", 0) or 0)
    snippets = evidence.get("positive_snippets", [])
    return len(snippets) if isinstance(snippets, list | tuple) else 0


def _candidate_score(candidate: Mapping[str, Any]) -> float:
    if not isinstance(candidate, Mapping):
        return 0.0
    try:
        return float(candidate.get("score", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _bounded_score(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError) as exc:
        raise ReviewDatasetError("quality_score must be a number") from exc
    if score < 0.0:
        return 0.0
    if score > 1.0:
        return 1.0
    return score


def _clean_text(value: str, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ReviewDatasetError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ReviewDatasetError(f"{field_name} must not be empty")
    return cleaned


__all__ = [
    "ReviewDatasetError",
    "ReviewDatasetExample",
    "ReviewDatasetQuality",
    "ReviewDatasetQualitySummary",
    "ReviewDatasetReport",
    "build_review_dataset",
    "evaluate_review_event_quality",
    "export_review_dataset_jsonl",
    "review_dataset_example_from_event",
]
