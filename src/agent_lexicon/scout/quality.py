"""Candidate quality signals for local scout ranking.

The quality layer is dependency-light by default and focuses on signals that
help reviewers see the most valuable terminology candidates first: code-like
shape, OOV/tokenizer fragmentation, lightweight clustering, and inbox priority.
Optional tokenizer-backed OOV scoring is Scout-only and never sits on the
runtime resolver/tool-guard path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from agent_lexicon.scout.oov import OovScoreResult, ProxyOovScorer
from agent_lexicon.text import surface_fragments


class CandidateQualityError(ValueError):
    """Raised when candidate quality scoring receives invalid input."""


class CandidatePriority(str, Enum):
    """Review priority bucket for a candidate."""

    IMPORTANT = "important"
    LATER = "later"


@dataclass(frozen=True, slots=True)
class CandidateQualitySignals:
    """Quality signals attached to one candidate surface."""

    normalized_surface: str
    cluster_key: str
    token_fragmentation_score: float
    oov_proxy_score: float
    surface_shape_score: float
    surface_risk_score: float
    priority_score: float
    priority: CandidatePriority
    priority_reasons: tuple[str, ...] = ()
    oov_score: float | None = None
    oov_source: str = "proxy"
    oov_tokenizer_score: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "normalized_surface", _clean_text(self.normalized_surface, field_name="normalized_surface"))
        object.__setattr__(self, "cluster_key", _clean_text(self.cluster_key, field_name="cluster_key"))
        object.__setattr__(self, "token_fragmentation_score", _bounded_float(self.token_fragmentation_score, field_name="token_fragmentation_score"))
        object.__setattr__(self, "oov_proxy_score", _bounded_float(self.oov_proxy_score, field_name="oov_proxy_score"))
        if self.oov_score is None:
            object.__setattr__(self, "oov_score", self.oov_proxy_score)
        else:
            object.__setattr__(self, "oov_score", _bounded_float(self.oov_score, field_name="oov_score"))
        if self.oov_tokenizer_score is not None:
            object.__setattr__(self, "oov_tokenizer_score", _bounded_float(self.oov_tokenizer_score, field_name="oov_tokenizer_score"))
        object.__setattr__(self, "oov_source", _clean_text(self.oov_source, field_name="oov_source"))
        object.__setattr__(self, "surface_shape_score", _bounded_float(self.surface_shape_score, field_name="surface_shape_score"))
        object.__setattr__(self, "surface_risk_score", _bounded_float(self.surface_risk_score, field_name="surface_risk_score"))
        object.__setattr__(self, "priority_score", _bounded_float(self.priority_score, field_name="priority_score"))
        object.__setattr__(self, "priority", CandidatePriority(self.priority.value if isinstance(self.priority, CandidatePriority) else str(self.priority)))
        if not isinstance(self.priority_reasons, tuple):
            object.__setattr__(self, "priority_reasons", tuple(str(reason) for reason in self.priority_reasons))
        if not isinstance(self.metadata, Mapping):
            raise CandidateQualityError("metadata must be a mapping")
        object.__setattr__(self, "metadata", {str(key): value for key, value in self.metadata.items()})

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable quality signal representation."""
        return {
            "normalized_surface": self.normalized_surface,
            "cluster_key": self.cluster_key,
            "token_fragmentation_score": self.token_fragmentation_score,
            "oov_proxy_score": self.oov_proxy_score,
            "oov_score": self.oov_score,
            "oov_source": self.oov_source,
            "oov_tokenizer_score": self.oov_tokenizer_score,
            "surface_shape_score": self.surface_shape_score,
            "surface_risk_score": self.surface_risk_score,
            "priority_score": self.priority_score,
            "priority": self.priority.value,
            "priority_reasons": list(self.priority_reasons),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class CandidateCluster:
    """A lightweight cluster of surfaces that look like the same entity."""

    cluster_key: str
    representative_surface: str
    surfaces: tuple[str, ...]
    normalized_surfaces: tuple[str, ...]
    candidate_count: int
    top_score: float
    occurrence_count: int
    document_count: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "cluster_key", _clean_text(self.cluster_key, field_name="cluster_key"))
        object.__setattr__(self, "representative_surface", _clean_text(self.representative_surface, field_name="representative_surface"))
        if not isinstance(self.surfaces, tuple):
            object.__setattr__(self, "surfaces", tuple(str(surface) for surface in self.surfaces))
        if not isinstance(self.normalized_surfaces, tuple):
            object.__setattr__(self, "normalized_surfaces", tuple(str(surface) for surface in self.normalized_surfaces))
        if self.candidate_count < 1:
            raise CandidateQualityError("candidate_count must be greater than 0")
        object.__setattr__(self, "top_score", _bounded_float(self.top_score, field_name="top_score"))
        if self.occurrence_count < 1:
            raise CandidateQualityError("occurrence_count must be greater than 0")
        if self.document_count < 1:
            raise CandidateQualityError("document_count must be greater than 0")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable cluster representation."""
        return {
            "cluster_key": self.cluster_key,
            "representative_surface": self.representative_surface,
            "surfaces": list(self.surfaces),
            "normalized_surfaces": list(self.normalized_surfaces),
            "candidate_count": self.candidate_count,
            "top_score": self.top_score,
            "occurrence_count": self.occurrence_count,
            "document_count": self.document_count,
        }


def compute_candidate_quality(
    *,
    surface: str,
    normalized_surface: str,
    kind: str,
    score: float,
    jargon_score: float,
    background_penalty: float,
    occurrence_count: int,
    document_count: int,
    negative_count: int = 0,
    cluster_size: int = 1,
    oov_result: OovScoreResult | None = None,
) -> CandidateQualitySignals:
    """Compute quality signals for one candidate.

    By default this uses the dependency-free proxy OOV signal. When Scout is
    configured with an optional tokenizer scorer, ``oov_result`` carries the
    tokenizer-backed score and metadata while the proxy score remains available
    for compatibility and comparison.
    """
    cleaned_surface = _clean_text(surface, field_name="surface")
    normalized = _clean_text(normalized_surface, field_name="normalized_surface")
    kind_value = _clean_text(kind, field_name="kind")
    if occurrence_count < 1:
        raise CandidateQualityError("occurrence_count must be greater than 0")
    if document_count < 1:
        raise CandidateQualityError("document_count must be greater than 0")
    if negative_count < 0:
        raise CandidateQualityError("negative_count must be greater than or equal to 0")
    if cluster_size < 1:
        raise CandidateQualityError("cluster_size must be greater than 0")

    fragments = surface_fragments(cleaned_surface)
    cluster_key = candidate_cluster_key(cleaned_surface)
    proxy_result = ProxyOovScorer().score(cleaned_surface)
    token_fragmentation_score = proxy_result.score
    surface_shape_score = _surface_shape_score(cleaned_surface, kind_value)
    oov_proxy_score = round(max(token_fragmentation_score, surface_shape_score * 0.92), 4)
    effective_oov_score = oov_proxy_score
    oov_source = "proxy"
    oov_tokenizer_score: float | None = None
    oov_metadata: Mapping[str, Any] | None = None
    if oov_result is not None:
        effective_oov_score = max(oov_proxy_score, oov_result.score)
        oov_source = oov_result.source
        oov_metadata = oov_result.to_dict()
        if oov_result.source == "tokenizer":
            oov_tokenizer_score = oov_result.score
    surface_risk_score = round(
        max(
            0.0,
            min(
                1.0,
                (0.46 * effective_oov_score)
                + (0.24 * surface_shape_score)
                + (0.16 * min(1.0, negative_count / 2.0))
                + (0.14 * min(1.0, cluster_size / 3.0)),
            ),
        ),
        4,
    )

    priority_score = round(
        max(
            0.0,
            min(
                1.0,
                (0.30 * _bounded_float(score, field_name="score"))
                + (0.22 * _bounded_float(jargon_score, field_name="jargon_score"))
                + (0.18 * effective_oov_score)
                + (0.12 * surface_risk_score)
                + (0.08 * min(1.0, document_count / 3.0))
                + (0.06 * min(1.0, occurrence_count / 5.0))
                + (0.04 * (1.0 if negative_count > 0 else 0.0))
                - (0.10 * _bounded_float(background_penalty, field_name="background_penalty")),
            ),
        ),
        4,
    )
    reasons = _priority_reasons(
        kind=kind_value,
        oov_score=effective_oov_score,
        oov_source=oov_source,
        surface_risk_score=surface_risk_score,
        jargon_score=float(jargon_score),
        document_count=document_count,
        negative_count=negative_count,
        cluster_size=cluster_size,
    )
    priority = CandidatePriority.IMPORTANT if priority_score >= 0.55 else CandidatePriority.LATER
    metadata: dict[str, Any] = {
        "fragment_count": len(fragments),
        "fragments": list(fragments),
        "negative_count": negative_count,
        "cluster_size": cluster_size,
        "signal_version": "quality-v2",
    }
    if oov_metadata is not None:
        metadata["oov"] = dict(oov_metadata)
    return CandidateQualitySignals(
        normalized_surface=normalized,
        cluster_key=cluster_key,
        token_fragmentation_score=token_fragmentation_score,
        oov_proxy_score=oov_proxy_score,
        oov_score=effective_oov_score,
        oov_source=oov_source,
        oov_tokenizer_score=oov_tokenizer_score,
        surface_shape_score=surface_shape_score,
        surface_risk_score=surface_risk_score,
        priority_score=priority_score,
        priority=priority,
        priority_reasons=reasons,
        metadata=metadata,
    )


def candidate_cluster_key(surface: str) -> str:
    """Return a stable, human-readable cluster key for similar surfaces."""
    fragments = surface_fragments(surface)
    if not fragments:
        return _normalize_surface(surface)
    canonical = [_singularize_token(fragment) for fragment in fragments if fragment]
    return " ".join(canonical) or _normalize_surface(surface)



def cluster_surface_records(records: Iterable[Mapping[str, Any]]) -> tuple[CandidateCluster, ...]:
    """Cluster mapping records with surface, score, occurrence_count, and document_count."""
    buckets: dict[str, list[Mapping[str, Any]]] = {}
    for record in records:
        surface = str(record.get("surface", "")).strip()
        if not surface:
            continue
        buckets.setdefault(candidate_cluster_key(surface), []).append(record)

    clusters: list[CandidateCluster] = []
    for key, bucket in buckets.items():
        sorted_bucket = sorted(bucket, key=lambda item: (-float(item.get("score", 0.0)), str(item.get("surface", "")).casefold()))
        representative = str(sorted_bucket[0].get("surface", ""))
        surfaces = tuple(str(item.get("surface", "")) for item in sorted_bucket)
        normalized_surfaces = tuple(str(item.get("normalized_surface", _normalize_surface(str(item.get("surface", ""))))) for item in sorted_bucket)
        clusters.append(
            CandidateCluster(
                cluster_key=key,
                representative_surface=representative,
                surfaces=surfaces,
                normalized_surfaces=normalized_surfaces,
                candidate_count=len(sorted_bucket),
                top_score=round(max(float(item.get("score", 0.0)) for item in sorted_bucket), 4),
                occurrence_count=sum(int(item.get("occurrence_count", 0)) for item in sorted_bucket),
                document_count=max(int(item.get("document_count", 1)) for item in sorted_bucket),
            )
        )
    clusters.sort(key=lambda cluster: (-cluster.top_score, -cluster.candidate_count, cluster.cluster_key))
    return tuple(clusters)


def _surface_shape_score(surface: str, kind: str) -> float:
    score = 0.0
    if kind in {"identifier", "acronym", "code"}:
        score += 0.36
    if "_" in surface or "-" in surface or "." in surface or ":" in surface or "/" in surface:
        score += 0.26
    if re.search(r"[a-z][A-Z]", surface):
        score += 0.22
    if re.fullmatch(r"[A-Z][A-Z0-9]{2,}", surface):
        score += 0.26
    if any(char.isdigit() for char in surface):
        score += 0.14
    if len(surface) <= 4 and surface.upper() == surface and any(char.isalpha() for char in surface):
        score += 0.14
    return round(max(0.0, min(1.0, score)), 4)


def _priority_reasons(
    *,
    kind: str,
    oov_score: float,
    oov_source: str,
    surface_risk_score: float,
    jargon_score: float,
    document_count: int,
    negative_count: int,
    cluster_size: int,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if kind in {"identifier", "acronym", "code"}:
        reasons.append("code_style_surface")
    if oov_score >= 0.55:
        reasons.append("high_oov_signal" if oov_source == "tokenizer" else "high_oov_proxy")
    if oov_source == "tokenizer":
        reasons.append("tokenizer_oov_signal")
    if oov_source == "proxy_fallback":
        reasons.append("oov_proxy_fallback")
    if surface_risk_score >= 0.55:
        reasons.append("high_surface_risk")
    if jargon_score >= 0.70:
        reasons.append("high_jargon_score")
    if document_count >= 2:
        reasons.append("multi_document_signal")
    if negative_count > 0:
        reasons.append("has_negative_evidence")
    if cluster_size > 1:
        reasons.append("clustered_variants")
    return tuple(reasons)


def _singularize_token(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _normalize_surface(value: str) -> str:
    return " ".join(str(value).strip().casefold().split())


def _clean_text(value: str, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise CandidateQualityError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise CandidateQualityError(f"{field_name} must not be empty")
    return cleaned


def _bounded_float(value: float, *, field_name: str) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise CandidateQualityError(f"{field_name} must be numeric") from exc
    if numeric < 0.0 or numeric > 1.0:
        raise CandidateQualityError(f"{field_name} must be between 0 and 1")
    return round(numeric, 4)


__all__ = [
    "CandidateCluster",
    "CandidatePriority",
    "CandidateQualityError",
    "CandidateQualitySignals",
    "candidate_cluster_key",
    "cluster_surface_records",
    "compute_candidate_quality",
    "surface_fragments",
]
