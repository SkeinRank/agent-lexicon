"""Consensus and abstention helpers for Review Agent decisions.

The consensus layer is intentionally provider-agnostic. It aggregates multiple
structured Review Agent responses when callers have them, and it can fall back to
the deterministic local reviewer when no model responses are supplied.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from agent_lexicon.workspace import WorkspaceReviewItem

from .agent import (
    ReviewAgentDecision,
    ReviewAgentError,
    ReviewAgentRecommendation,
    ReviewEvidenceSummary,
    run_review_agent,
)


class ReviewAgentConsensusStatus(str, Enum):
    """Status returned by a consensus review."""

    CONSENSUS = "consensus"
    ABSTAIN = "abstain"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class ReviewAgentConsensusReport:
    """Aggregated review decision with consensus and abstention metadata."""

    surface: str
    normalized_surface: str
    status: ReviewAgentConsensusStatus
    decision: ReviewAgentDecision
    samples: tuple[ReviewAgentDecision, ...]
    sample_count: int
    agreement_count: int
    agreement_ratio: float
    confidence: float
    reason: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "surface", _clean_non_empty(self.surface, field_name="surface"))
        object.__setattr__(self, "normalized_surface", _clean_non_empty(self.normalized_surface, field_name="normalized_surface"))
        object.__setattr__(
            self,
            "status",
            ReviewAgentConsensusStatus(self.status.value if isinstance(self.status, ReviewAgentConsensusStatus) else str(self.status)),
        )
        if not isinstance(self.decision, ReviewAgentDecision):
            raise ReviewAgentError("decision must be a ReviewAgentDecision")
        if not isinstance(self.samples, tuple):
            object.__setattr__(self, "samples", tuple(self.samples))
        for sample in self.samples:
            if not isinstance(sample, ReviewAgentDecision):
                raise ReviewAgentError("samples must contain ReviewAgentDecision objects")
        if self.sample_count < 0:
            raise ReviewAgentError("sample_count must be greater than or equal to 0")
        if self.agreement_count < 0:
            raise ReviewAgentError("agreement_count must be greater than or equal to 0")
        object.__setattr__(self, "agreement_ratio", _bounded_float(self.agreement_ratio))
        object.__setattr__(self, "confidence", _bounded_float(self.confidence))
        object.__setattr__(self, "reason", _clean_non_empty(self.reason, field_name="reason"))
        if not isinstance(self.metadata, Mapping):
            raise ReviewAgentError("metadata must be a mapping")
        object.__setattr__(self, "metadata", {str(key): value for key, value in self.metadata.items()})

    @property
    def abstained(self) -> bool:
        """Return whether the consensus layer refused to make a positive decision."""
        return self.status != ReviewAgentConsensusStatus.CONSENSUS

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable consensus report."""
        return {
            "surface": self.surface,
            "normalized_surface": self.normalized_surface,
            "status": self.status.value,
            "abstained": self.abstained,
            "decision": self.decision.to_dict(),
            "sample_count": self.sample_count,
            "agreement_count": self.agreement_count,
            "agreement_ratio": self.agreement_ratio,
            "confidence": self.confidence,
            "reason": self.reason,
            "samples": [sample.to_dict() for sample in self.samples],
            "metadata": dict(self.metadata),
        }

    def to_json(self) -> str:
        """Return this report as stable JSON."""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)


def run_review_agent_consensus(
    review_item: WorkspaceReviewItem,
    *,
    llm_responses: Sequence[str | Mapping[str, Any]] | None = None,
    min_agreement: float = 0.67,
    min_confidence: float = 0.65,
) -> ReviewAgentConsensusReport:
    """Aggregate Review Agent samples and abstain on unstable decisions.

    When llm_responses are provided, each response is parsed through
    run_review_agent so prompt-safety blocking and response validation stay in
    one place. When no responses are provided, the deterministic local reviewer
    is used as a single-sample pre-review decision.
    """
    if not isinstance(review_item, WorkspaceReviewItem):
        raise ReviewAgentError("review_item must be a WorkspaceReviewItem")
    if not 0 < min_agreement <= 1:
        raise ReviewAgentError("min_agreement must be greater than 0 and less than or equal to 1")
    if not 0 <= min_confidence <= 1:
        raise ReviewAgentError("min_confidence must be between 0 and 1")

    response_values = tuple(llm_responses or ())
    if response_values:
        samples = tuple(run_review_agent(review_item, llm_response=response) for response in response_values)
        source = "llm_responses"
    else:
        samples = (run_review_agent(review_item),)
        source = "deterministic_review_agent"

    if not samples:
        raise ReviewAgentError("at least one review sample is required")

    if any(not sample.llm_review_allowed for sample in samples):
        blocked = _blocked_decision(review_item, samples=samples)
        return ReviewAgentConsensusReport(
            surface=review_item.surface,
            normalized_surface=review_item.normalized_surface,
            status=ReviewAgentConsensusStatus.BLOCKED,
            decision=blocked,
            samples=samples,
            sample_count=len(samples),
            agreement_count=len(samples),
            agreement_ratio=1.0,
            confidence=blocked.confidence,
            reason="LLM review is blocked by prompt-safety signals.",
            metadata={"source": source, "min_agreement": min_agreement, "min_confidence": min_confidence},
        )

    groups: dict[tuple[str, str], list[ReviewAgentDecision]] = defaultdict(list)
    for sample in samples:
        groups[_decision_key(sample)].append(sample)
    top_key, top_samples = max(groups.items(), key=lambda item: (len(item[1]), _average_confidence(item[1])))
    agreement_count = len(top_samples)
    agreement_ratio = round(agreement_count / len(samples), 4)
    confidence = round(_average_confidence(top_samples), 4)

    if agreement_ratio >= min_agreement and confidence >= min_confidence:
        decision = _consensus_decision(review_item, top_samples=tuple(top_samples), key=top_key, confidence=confidence)
        return ReviewAgentConsensusReport(
            surface=review_item.surface,
            normalized_surface=review_item.normalized_surface,
            status=ReviewAgentConsensusStatus.CONSENSUS,
            decision=decision,
            samples=samples,
            sample_count=len(samples),
            agreement_count=agreement_count,
            agreement_ratio=agreement_ratio,
            confidence=decision.confidence,
            reason="Review samples reached the required agreement and confidence thresholds.",
            metadata={
                "source": source,
                "min_agreement": min_agreement,
                "min_confidence": min_confidence,
                "vote_counts": _vote_counts(samples),
            },
        )

    reason = "Review Agent abstained because samples did not reach the required agreement threshold."
    if confidence < min_confidence:
        reason = "Review Agent abstained because the top decision confidence is below the required threshold."
    decision = _abstain_decision(review_item, samples=samples, confidence=min(confidence, 0.55), reason=reason)
    return ReviewAgentConsensusReport(
        surface=review_item.surface,
        normalized_surface=review_item.normalized_surface,
        status=ReviewAgentConsensusStatus.ABSTAIN,
        decision=decision,
        samples=samples,
        sample_count=len(samples),
        agreement_count=agreement_count,
        agreement_ratio=agreement_ratio,
        confidence=decision.confidence,
        reason=reason,
        metadata={
            "source": source,
            "min_agreement": min_agreement,
            "min_confidence": min_confidence,
            "vote_counts": _vote_counts(samples),
        },
    )


def _decision_key(decision: ReviewAgentDecision) -> tuple[str, str]:
    canonical_key = ""
    if decision.recommendation == ReviewAgentRecommendation.ACCEPT:
        canonical_key = " ".join(decision.canonical_name.casefold().split())
    return (decision.recommendation.value, canonical_key)


def _vote_counts(samples: Sequence[ReviewAgentDecision]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for sample in samples:
        recommendation, canonical = _decision_key(sample)
        key = recommendation if not canonical else f"{recommendation}:{canonical}"
        counter[key] += 1
    return dict(sorted(counter.items()))


def _average_confidence(samples: Sequence[ReviewAgentDecision]) -> float:
    if not samples:
        return 0.0
    return sum(sample.confidence for sample in samples) / len(samples)


def _consensus_decision(
    review_item: WorkspaceReviewItem,
    *,
    top_samples: tuple[ReviewAgentDecision, ...],
    key: tuple[str, str],
    confidence: float,
) -> ReviewAgentDecision:
    recommendation = ReviewAgentRecommendation(key[0])
    canonical_name = _best_canonical_name(review_item, top_samples=top_samples, recommendation=recommendation)
    risk_flags = _merged_risk_flags(top_samples)
    if len(top_samples) > 1:
        risk_flags = tuple(sorted({*risk_flags, "consensus"}))
    note = _consensus_note(top_samples)
    evidence_summary = _first_evidence_summary(top_samples)
    return ReviewAgentDecision(
        surface=review_item.surface,
        normalized_surface=review_item.normalized_surface,
        recommendation=recommendation,
        confidence=confidence,
        canonical_name=canonical_name,
        reviewer_note=note,
        risk_flags=risk_flags,
        llm_review_allowed=True,
        evidence_summary=evidence_summary,
        metadata={
            "source": "review_agent_consensus",
            "sample_count": len(top_samples),
            "agreement_recommendation": recommendation.value,
        },
    )


def _abstain_decision(
    review_item: WorkspaceReviewItem,
    *,
    samples: tuple[ReviewAgentDecision, ...],
    confidence: float,
    reason: str,
) -> ReviewAgentDecision:
    return ReviewAgentDecision(
        surface=review_item.surface,
        normalized_surface=review_item.normalized_surface,
        recommendation=ReviewAgentRecommendation.NEEDS_MORE_EVIDENCE,
        confidence=max(0.25, min(0.55, confidence)),
        canonical_name=_best_canonical_name(review_item, top_samples=samples, recommendation=ReviewAgentRecommendation.NEEDS_MORE_EVIDENCE),
        reviewer_note=reason,
        risk_flags=tuple(sorted({*_merged_risk_flags(samples), "abstained"})),
        llm_review_allowed=True,
        evidence_summary=_first_evidence_summary(samples),
        metadata={"source": "review_agent_consensus", "sample_count": len(samples), "abstained": True},
    )


def _blocked_decision(review_item: WorkspaceReviewItem, *, samples: tuple[ReviewAgentDecision, ...]) -> ReviewAgentDecision:
    return ReviewAgentDecision(
        surface=review_item.surface,
        normalized_surface=review_item.normalized_surface,
        recommendation=ReviewAgentRecommendation.NEEDS_MORE_EVIDENCE,
        confidence=min(0.4, max((sample.confidence for sample in samples), default=0.4)),
        canonical_name=_best_canonical_name(review_item, top_samples=samples, recommendation=ReviewAgentRecommendation.NEEDS_MORE_EVIDENCE),
        reviewer_note="LLM review is blocked because evidence contains high-risk prompt-injection indicators.",
        risk_flags=tuple(sorted({*_merged_risk_flags(samples), "prompt_injection_high", "abstained"})),
        llm_review_allowed=False,
        evidence_summary=_first_evidence_summary(samples),
        metadata={"source": "review_agent_consensus_blocked", "sample_count": len(samples), "abstained": True},
    )


def _best_canonical_name(
    review_item: WorkspaceReviewItem,
    *,
    top_samples: Sequence[ReviewAgentDecision],
    recommendation: ReviewAgentRecommendation,
) -> str:
    if recommendation == ReviewAgentRecommendation.REJECT:
        return ""
    names = [sample.canonical_name.strip() for sample in top_samples if sample.canonical_name.strip()]
    if names:
        return Counter(names).most_common(1)[0][0]
    return " ".join(review_item.surface.replace("_", " ").replace("-", " ").replace(".", " ").split()).casefold()


def _consensus_note(samples: Sequence[ReviewAgentDecision]) -> str:
    notes = [sample.reviewer_note.strip() for sample in samples if sample.reviewer_note.strip()]
    if not notes:
        return "Review samples reached consensus."
    most_common = Counter(notes).most_common(1)[0][0]
    return f"Review samples reached consensus. {most_common}"


def _merged_risk_flags(samples: Sequence[ReviewAgentDecision]) -> tuple[str, ...]:
    flags: set[str] = set()
    for sample in samples:
        flags.update(sample.risk_flags)
    return tuple(sorted(flags))


def _first_evidence_summary(samples: Sequence[ReviewAgentDecision]) -> ReviewEvidenceSummary | None:
    for sample in samples:
        if sample.evidence_summary is not None:
            return sample.evidence_summary
    return None


def _clean_non_empty(value: str, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ReviewAgentError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ReviewAgentError(f"{field_name} is required")
    return cleaned


def _bounded_float(value: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ReviewAgentError("numeric value must be a number") from exc
    return max(0.0, min(1.0, round(numeric, 4)))


__all__ = [
    "ReviewAgentConsensusReport",
    "ReviewAgentConsensusStatus",
    "run_review_agent_consensus",
]
