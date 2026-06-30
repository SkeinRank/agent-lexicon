"""Scout quality reports for candidate discovery.

The Scout quality report turns candidate, clustering, OOV, priority, and evidence
signals into a compact metrics view. It is designed for local product workflows:
reviewers should see how many raw surfaces were reduced to important review
items, whether evidence is available, and which quality signals explain the
ranking.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from agent_lexicon.scout.candidates import CandidateSurfaceKind, ScoutCandidate, ScoutCandidateReport
from agent_lexicon.scout.evidence import EvidencePack, EvidencePackReport


class ScoutQualityReportError(ValueError):
    """Raised when a Scout quality report cannot be built."""


@dataclass(frozen=True, slots=True)
class ScoutQualityCandidateSummary:
    """Compact quality summary for one candidate shown in reports."""

    surface: str
    normalized_surface: str
    priority: str
    priority_score: float
    score: float
    jargon_score: float
    oov_score: float
    oov_source: str
    surface_risk_score: float
    evidence_status: str
    positive_count: int
    negative_count: int
    cluster_key: str | None = None
    cluster_size: int = 1
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "surface", _clean_text(self.surface, field_name="surface"))
        object.__setattr__(self, "normalized_surface", _clean_text(self.normalized_surface, field_name="normalized_surface"))
        object.__setattr__(self, "priority", _clean_text(self.priority, field_name="priority"))
        object.__setattr__(self, "priority_score", _bounded_float(self.priority_score, field_name="priority_score"))
        object.__setattr__(self, "score", _bounded_float(self.score, field_name="score"))
        object.__setattr__(self, "jargon_score", _bounded_float(self.jargon_score, field_name="jargon_score"))
        object.__setattr__(self, "oov_score", _bounded_float(self.oov_score, field_name="oov_score"))
        object.__setattr__(self, "oov_source", _clean_text(self.oov_source, field_name="oov_source"))
        object.__setattr__(self, "surface_risk_score", _bounded_float(self.surface_risk_score, field_name="surface_risk_score"))
        object.__setattr__(self, "evidence_status", _clean_text(self.evidence_status, field_name="evidence_status"))
        if self.positive_count < 0:
            raise ScoutQualityReportError("positive_count must be greater than or equal to 0")
        if self.negative_count < 0:
            raise ScoutQualityReportError("negative_count must be greater than or equal to 0")
        if self.cluster_key is not None:
            object.__setattr__(self, "cluster_key", _clean_text(self.cluster_key, field_name="cluster_key"))
        if self.cluster_size < 1:
            raise ScoutQualityReportError("cluster_size must be greater than 0")
        if not isinstance(self.reasons, tuple):
            object.__setattr__(self, "reasons", tuple(str(reason) for reason in self.reasons))

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable candidate summary."""
        return {
            "surface": self.surface,
            "normalized_surface": self.normalized_surface,
            "priority": self.priority,
            "priority_score": self.priority_score,
            "score": self.score,
            "jargon_score": self.jargon_score,
            "oov_score": self.oov_score,
            "oov_source": self.oov_source,
            "surface_risk_score": self.surface_risk_score,
            "evidence_status": self.evidence_status,
            "positive_count": self.positive_count,
            "negative_count": self.negative_count,
            "cluster_key": self.cluster_key,
            "cluster_size": self.cluster_size,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True, slots=True)
class ScoutQualityReport:
    """Aggregate quality metrics for a Scout run or workspace inbox."""

    document_count: int
    candidate_count: int
    important_count: int
    later_count: int
    cluster_count: int
    clustered_candidate_count: int
    code_style_count: int
    high_oov_count: int
    tokenizer_oov_count: int
    proxy_fallback_count: int
    high_surface_risk_count: int
    high_jargon_count: int
    multi_document_count: int
    negative_evidence_candidate_count: int
    evidence_pack_count: int
    evidence_coverage: float
    positive_snippet_count: int
    negative_snippet_count: int
    unsafe_evidence_pack_count: int = 0
    prompt_safety_high_count: int = 0
    reason_counts: Mapping[str, int] = field(default_factory=dict)
    top_candidates: tuple[ScoutQualityCandidateSummary, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in (
            "document_count",
            "candidate_count",
            "important_count",
            "later_count",
            "cluster_count",
            "clustered_candidate_count",
            "code_style_count",
            "high_oov_count",
            "tokenizer_oov_count",
            "proxy_fallback_count",
            "high_surface_risk_count",
            "high_jargon_count",
            "multi_document_count",
            "negative_evidence_candidate_count",
            "evidence_pack_count",
            "positive_snippet_count",
            "negative_snippet_count",
            "unsafe_evidence_pack_count",
            "prompt_safety_high_count",
        ):
            value = int(getattr(self, field_name))
            if value < 0:
                raise ScoutQualityReportError(f"{field_name} must be greater than or equal to 0")
            object.__setattr__(self, field_name, value)
        object.__setattr__(self, "evidence_coverage", _bounded_float(self.evidence_coverage, field_name="evidence_coverage"))
        if self.important_count + self.later_count > self.candidate_count:
            raise ScoutQualityReportError("important_count + later_count must not exceed candidate_count")
        if not isinstance(self.reason_counts, Mapping):
            raise ScoutQualityReportError("reason_counts must be a mapping")
        object.__setattr__(self, "reason_counts", {str(key): int(value) for key, value in self.reason_counts.items()})
        if not isinstance(self.top_candidates, tuple):
            object.__setattr__(self, "top_candidates", tuple(self.top_candidates))
        for candidate in self.top_candidates:
            if not isinstance(candidate, ScoutQualityCandidateSummary):
                raise ScoutQualityReportError("top_candidates must contain ScoutQualityCandidateSummary objects")
        if not isinstance(self.metadata, Mapping):
            raise ScoutQualityReportError("metadata must be a mapping")
        object.__setattr__(self, "metadata", {str(key): value for key, value in self.metadata.items()})

    @property
    def important_ratio(self) -> float:
        """Return the share of candidates that are important."""
        return _ratio(self.important_count, self.candidate_count)

    @property
    def later_ratio(self) -> float:
        """Return the share of candidates that are lower priority."""
        return _ratio(self.later_count, self.candidate_count)

    @property
    def review_reduction_ratio(self) -> float:
        """Return how much candidate volume is moved out of Important review."""
        return round(1.0 - self.important_ratio, 4) if self.candidate_count else 0.0

    @property
    def negative_evidence_ratio(self) -> float:
        """Return the share of candidates with negative evidence."""
        return _ratio(self.negative_evidence_candidate_count, self.candidate_count)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable quality report."""
        return {
            "document_count": self.document_count,
            "candidate_count": self.candidate_count,
            "important_count": self.important_count,
            "important_ratio": self.important_ratio,
            "later_count": self.later_count,
            "later_ratio": self.later_ratio,
            "review_reduction_ratio": self.review_reduction_ratio,
            "cluster_count": self.cluster_count,
            "clustered_candidate_count": self.clustered_candidate_count,
            "code_style_count": self.code_style_count,
            "high_oov_count": self.high_oov_count,
            "tokenizer_oov_count": self.tokenizer_oov_count,
            "proxy_fallback_count": self.proxy_fallback_count,
            "high_surface_risk_count": self.high_surface_risk_count,
            "high_jargon_count": self.high_jargon_count,
            "multi_document_count": self.multi_document_count,
            "negative_evidence_candidate_count": self.negative_evidence_candidate_count,
            "negative_evidence_ratio": self.negative_evidence_ratio,
            "evidence_pack_count": self.evidence_pack_count,
            "evidence_coverage": self.evidence_coverage,
            "positive_snippet_count": self.positive_snippet_count,
            "negative_snippet_count": self.negative_snippet_count,
            "unsafe_evidence_pack_count": self.unsafe_evidence_pack_count,
            "prompt_safety_high_count": self.prompt_safety_high_count,
            "reason_counts": dict(sorted(self.reason_counts.items())),
            "top_candidates": [candidate.to_dict() for candidate in self.top_candidates],
            "metadata": dict(self.metadata),
        }

    def to_json(self) -> str:
        """Return this quality report as formatted JSON."""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)

    def to_text(self, *, top_limit: int = 5) -> str:
        """Return a compact product-facing text report."""
        lines = [
            "Scout quality report:",
            f"- Candidates: {self.candidate_count} total, {self.important_count} important, {self.later_count} later",
            f"- Review reduction: {self.review_reduction_ratio:.1%} moved out of Important review",
            f"- Clusters: {self.cluster_count} clusters, {self.clustered_candidate_count} clustered candidates",
            f"- Signals: {self.code_style_count} code-style, {self.high_oov_count} high-OOV, {self.high_surface_risk_count} high-risk, {self.high_jargon_count} high-jargon",
            f"- Evidence: {self.evidence_pack_count}/{self.candidate_count} candidates covered ({self.evidence_coverage:.1%}), {self.positive_snippet_count} positive, {self.negative_snippet_count} negative snippets",
        ]
        if self.tokenizer_oov_count or self.proxy_fallback_count:
            lines.append(
                f"- OOV source: {self.tokenizer_oov_count} tokenizer-scored, {self.proxy_fallback_count} proxy fallback"
            )
        if self.unsafe_evidence_pack_count or self.prompt_safety_high_count:
            lines.append(
                f"- Prompt safety: {self.unsafe_evidence_pack_count} unsafe packs, {self.prompt_safety_high_count} high-risk findings"
            )
        if self.reason_counts:
            top_reasons = sorted(self.reason_counts.items(), key=lambda item: (-item[1], item[0]))[:5]
            lines.append("- Top reasons: " + ", ".join(f"{name}={count}" for name, count in top_reasons))
        if self.top_candidates and top_limit > 0:
            lines.append("- Top candidates:")
            for candidate in self.top_candidates[:top_limit]:
                lines.append(
                    f"  [{candidate.priority.upper()}] {candidate.surface} "
                    f"priority={candidate.priority_score:.3f} "
                    f"oov={candidate.oov_score:.3f} "
                    f"evidence={candidate.positive_count}+/{candidate.negative_count}-"
                )
        return "\n".join(lines)


def build_scout_quality_report(
    candidate_report: ScoutCandidateReport,
    evidence_report: EvidencePackReport | None = None,
    *,
    max_top_candidates: int = 10,
) -> ScoutQualityReport:
    """Build a quality report from candidate discovery and optional evidence."""
    if not isinstance(candidate_report, ScoutCandidateReport):
        raise ScoutQualityReportError("candidate_report must be a ScoutCandidateReport")
    if evidence_report is not None and not isinstance(evidence_report, EvidencePackReport):
        raise ScoutQualityReportError("evidence_report must be an EvidencePackReport")
    evidence_by_surface = _evidence_by_surface(evidence_report.packs if evidence_report else ())
    return _build_report_from_records(
        _records_from_candidates(candidate_report.candidates, evidence_by_surface=evidence_by_surface),
        document_count=candidate_report.document_count,
        cluster_count=candidate_report.cluster_count,
        evidence_report=evidence_report,
        max_top_candidates=max_top_candidates,
        metadata={
            "source": "candidate_report",
            "candidate_report": dict(candidate_report.metadata),
            "evidence_report": dict(evidence_report.metadata) if evidence_report is not None else {},
        },
    )


def build_scout_quality_report_from_review_items(
    review_items: Iterable[Any],
    *,
    document_count: int | None = None,
    max_top_candidates: int = 10,
) -> ScoutQualityReport:
    """Build a quality report from workspace review items."""
    records = tuple(_record_from_review_item(item) for item in review_items)
    cluster_keys = {str(record.get("cluster_key", "")) for record in records if str(record.get("cluster_key", "")).strip()}
    inferred_document_count = document_count if document_count is not None else max((int(record["document_count"]) for record in records), default=0)
    return _build_report_from_records(
        records,
        document_count=inferred_document_count,
        cluster_count=len(cluster_keys),
        evidence_report=None,
        max_top_candidates=max_top_candidates,
        metadata={"source": "workspace_review_items"},
    )


def _build_report_from_records(
    records: Iterable[Mapping[str, Any]],
    *,
    document_count: int,
    cluster_count: int,
    evidence_report: EvidencePackReport | None,
    max_top_candidates: int,
    metadata: Mapping[str, Any],
) -> ScoutQualityReport:
    record_tuple = tuple(records)
    if max_top_candidates < 1:
        raise ScoutQualityReportError("max_top_candidates must be greater than 0")
    candidate_count = len(record_tuple)
    important_count = sum(1 for record in record_tuple if record["priority"] == "important")
    later_count = sum(1 for record in record_tuple if record["priority"] == "later")
    code_style_count = sum(1 for record in record_tuple if record["kind"] in _CODE_STYLE_KINDS)
    high_oov_count = sum(1 for record in record_tuple if float(record["oov_score"]) >= 0.55)
    tokenizer_oov_count = sum(1 for record in record_tuple if record["oov_source"] == "tokenizer")
    proxy_fallback_count = sum(1 for record in record_tuple if record["oov_source"] == "proxy_fallback")
    high_surface_risk_count = sum(1 for record in record_tuple if float(record["surface_risk_score"]) >= 0.55)
    high_jargon_count = sum(1 for record in record_tuple if float(record["jargon_score"]) >= 0.70)
    multi_document_count = sum(1 for record in record_tuple if int(record["document_count"]) >= 2)
    clustered_candidate_count = sum(1 for record in record_tuple if int(record["cluster_size"]) > 1)
    negative_evidence_candidate_count = sum(1 for record in record_tuple if int(record["negative_count"]) > 0)
    evidence_pack_count = sum(1 for record in record_tuple if int(record["positive_count"]) + int(record["negative_count"]) > 0)
    positive_snippet_count = sum(int(record["positive_count"]) for record in record_tuple)
    negative_snippet_count = sum(int(record["negative_count"]) for record in record_tuple)
    reason_counts: dict[str, int] = {}
    for record in record_tuple:
        for reason in record["reasons"]:
            reason_counts[str(reason)] = reason_counts.get(str(reason), 0) + 1
    unsafe_evidence_pack_count = 0
    prompt_safety_high_count = 0
    if evidence_report is not None:
        prompt_safety = dict(evidence_report.metadata.get("prompt_safety", {}))
        unsafe_evidence_pack_count = int(prompt_safety.get("unsafe_pack_count", 0) or 0)
        prompt_safety_high_count = int(prompt_safety.get("high_count", 0) or 0)
    else:
        unsafe_evidence_pack_count = sum(1 for record in record_tuple if bool(record.get("unsafe_evidence_pack")))
        prompt_safety_high_count = sum(int(record.get("prompt_safety_high_count", 0) or 0) for record in record_tuple)
    top_candidates = tuple(
        _summary_from_record(record)
        for record in sorted(
            record_tuple,
            key=lambda item: (item["priority"] != "important", -float(item["priority_score"]), -float(item["score"]), str(item["surface"]).casefold()),
        )[:max_top_candidates]
    )
    return ScoutQualityReport(
        document_count=document_count,
        candidate_count=candidate_count,
        important_count=important_count,
        later_count=later_count,
        cluster_count=cluster_count,
        clustered_candidate_count=clustered_candidate_count,
        code_style_count=code_style_count,
        high_oov_count=high_oov_count,
        tokenizer_oov_count=tokenizer_oov_count,
        proxy_fallback_count=proxy_fallback_count,
        high_surface_risk_count=high_surface_risk_count,
        high_jargon_count=high_jargon_count,
        multi_document_count=multi_document_count,
        negative_evidence_candidate_count=negative_evidence_candidate_count,
        evidence_pack_count=evidence_pack_count,
        evidence_coverage=_ratio(evidence_pack_count, candidate_count),
        positive_snippet_count=positive_snippet_count,
        negative_snippet_count=negative_snippet_count,
        unsafe_evidence_pack_count=unsafe_evidence_pack_count,
        prompt_safety_high_count=prompt_safety_high_count,
        reason_counts=reason_counts,
        top_candidates=top_candidates,
        metadata=metadata,
    )


def _records_from_candidates(
    candidates: Iterable[ScoutCandidate],
    *,
    evidence_by_surface: Mapping[str, EvidencePack],
) -> tuple[Mapping[str, Any], ...]:
    records: list[Mapping[str, Any]] = []
    for candidate in candidates:
        quality = _quality_metadata_from_payload(candidate.to_dict())
        cluster = _cluster_metadata_from_payload(candidate.to_dict())
        evidence = evidence_by_surface.get(candidate.normalized_surface)
        records.append(
            {
                "surface": candidate.surface,
                "normalized_surface": candidate.normalized_surface,
                "kind": candidate.kind.value,
                "score": candidate.score,
                "jargon_score": candidate.jargon_score,
                "document_count": candidate.document_count,
                "priority": _priority_value(quality),
                "priority_score": _quality_float(quality, "priority_score", candidate.score),
                "reasons": tuple(str(reason) for reason in quality.get("priority_reasons", ())),
                "cluster_key": str(quality.get("cluster_key") or cluster.get("cluster_key") or ""),
                "cluster_size": int(cluster.get("candidate_count", quality.get("metadata", {}).get("cluster_size", 1)) or 1),
                "oov_score": _quality_float(quality, "oov_score", _quality_float(quality, "oov_proxy_score", 0.0)),
                "oov_source": str(quality.get("oov_source", "proxy") or "proxy"),
                "surface_risk_score": _quality_float(quality, "surface_risk_score", 0.0),
                "positive_count": evidence.positive_count if evidence else 0,
                "negative_count": evidence.negative_count if evidence else 0,
                "unsafe_evidence_pack": _unsafe_pack(evidence) if evidence else False,
                "prompt_safety_high_count": _prompt_safety_high_count(evidence) if evidence else 0,
            }
        )
    return tuple(records)


def _record_from_review_item(review_item: Any) -> Mapping[str, Any]:
    payload = getattr(review_item, "candidate_payload", {})
    quality = _quality_metadata_from_payload(payload)
    cluster = _cluster_metadata_from_payload(payload)
    evidence_payload = getattr(review_item, "evidence_payload", {})
    return {
        "surface": str(getattr(review_item, "surface")),
        "normalized_surface": str(getattr(review_item, "normalized_surface")),
        "kind": str(getattr(review_item, "candidate_kind")),
        "score": float(getattr(review_item, "score")),
        "jargon_score": float(getattr(review_item, "jargon_score")),
        "document_count": int(getattr(review_item, "document_count")),
        "priority": _priority_value(quality),
        "priority_score": _quality_float(quality, "priority_score", float(getattr(review_item, "score"))),
        "reasons": tuple(str(reason) for reason in quality.get("priority_reasons", ())),
        "cluster_key": str(quality.get("cluster_key") or cluster.get("cluster_key") or ""),
        "cluster_size": int(cluster.get("candidate_count", quality.get("metadata", {}).get("cluster_size", 1)) or 1),
        "oov_score": _quality_float(quality, "oov_score", _quality_float(quality, "oov_proxy_score", 0.0)),
        "oov_source": str(quality.get("oov_source", "proxy") or "proxy"),
        "surface_risk_score": _quality_float(quality, "surface_risk_score", 0.0),
        "positive_count": int(getattr(review_item, "positive_count")),
        "negative_count": int(getattr(review_item, "negative_count")),
        "unsafe_evidence_pack": _unsafe_payload(evidence_payload),
        "prompt_safety_high_count": _payload_high_count(evidence_payload),
    }


def _summary_from_record(record: Mapping[str, Any]) -> ScoutQualityCandidateSummary:
    positive_count = int(record["positive_count"])
    negative_count = int(record["negative_count"])
    evidence_status = "covered" if positive_count + negative_count > 0 else "missing"
    cluster_key = str(record.get("cluster_key") or "").strip() or None
    return ScoutQualityCandidateSummary(
        surface=str(record["surface"]),
        normalized_surface=str(record["normalized_surface"]),
        priority=str(record["priority"]),
        priority_score=float(record["priority_score"]),
        score=float(record["score"]),
        jargon_score=float(record["jargon_score"]),
        oov_score=float(record["oov_score"]),
        oov_source=str(record["oov_source"]),
        surface_risk_score=float(record["surface_risk_score"]),
        evidence_status=evidence_status,
        positive_count=positive_count,
        negative_count=negative_count,
        cluster_key=cluster_key,
        cluster_size=int(record["cluster_size"]),
        reasons=tuple(str(reason) for reason in record["reasons"]),
    )


def _evidence_by_surface(packs: Iterable[EvidencePack]) -> dict[str, EvidencePack]:
    return {pack.normalized_surface: pack for pack in packs}


def _quality_metadata_from_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(payload, Mapping):
        metadata = payload.get("metadata", {})
        if isinstance(metadata, Mapping):
            quality = metadata.get("quality", {})
            if isinstance(quality, Mapping):
                return quality
    return {}


def _cluster_metadata_from_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(payload, Mapping):
        metadata = payload.get("metadata", {})
        if isinstance(metadata, Mapping):
            cluster = metadata.get("cluster", {})
            if isinstance(cluster, Mapping):
                return cluster
    return {}


def _priority_value(quality: Mapping[str, Any]) -> str:
    value = str(quality.get("priority", "later") or "later")
    return value if value in {"important", "later"} else "later"


def _quality_float(quality: Mapping[str, Any], key: str, default: float) -> float:
    try:
        value = float(quality.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(0.0, min(1.0, round(value, 4)))


def _unsafe_pack(evidence: EvidencePack) -> bool:
    prompt_safety = dict(evidence.metadata.get("prompt_safety", {}))
    return prompt_safety.get("is_safe_for_llm_review") is False


def _prompt_safety_high_count(evidence: EvidencePack) -> int:
    prompt_safety = dict(evidence.metadata.get("prompt_safety", {}))
    return int(prompt_safety.get("high_count", 0) or 0)


def _unsafe_payload(payload: Mapping[str, Any]) -> bool:
    metadata = payload.get("metadata", {}) if isinstance(payload, Mapping) else {}
    prompt_safety = metadata.get("prompt_safety", {}) if isinstance(metadata, Mapping) else {}
    return prompt_safety.get("is_safe_for_llm_review") is False if isinstance(prompt_safety, Mapping) else False


def _payload_high_count(payload: Mapping[str, Any]) -> int:
    metadata = payload.get("metadata", {}) if isinstance(payload, Mapping) else {}
    prompt_safety = metadata.get("prompt_safety", {}) if isinstance(metadata, Mapping) else {}
    if not isinstance(prompt_safety, Mapping):
        return 0
    return int(prompt_safety.get("high_count", 0) or 0)


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(max(0.0, min(1.0, numerator / denominator)), 4)


def _clean_text(value: str, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ScoutQualityReportError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ScoutQualityReportError(f"{field_name} must not be empty")
    return cleaned


def _bounded_float(value: float, *, field_name: str) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ScoutQualityReportError(f"{field_name} must be numeric") from exc
    if numeric < 0.0 or numeric > 1.0:
        raise ScoutQualityReportError(f"{field_name} must be between 0 and 1")
    return round(numeric, 4)


_CODE_STYLE_KINDS = {
    CandidateSurfaceKind.IDENTIFIER.value,
    CandidateSurfaceKind.ACRONYM.value,
    CandidateSurfaceKind.CODE.value,
}


__all__ = [
    "ScoutQualityCandidateSummary",
    "ScoutQualityReport",
    "ScoutQualityReportError",
    "build_scout_quality_report",
    "build_scout_quality_report_from_review_items",
]
