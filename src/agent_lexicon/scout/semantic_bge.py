"""Optional BGE backend for semantic near-miss reranking.

The backend is intentionally lazy and optional. Agent Lexicon keeps the default
runtime dependency-free; callers opt in from offline Scout workflows when they
want semantic reranking for gray-zone near-miss suggestions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Mapping, Sequence

from .semantic import (
    SemanticNearMissCandidate,
    SemanticNearMissError,
    SemanticNearMissRequest,
    SemanticNearMissResult,
    SemanticSuggestionSource,
)

DEFAULT_BGE_BASE_MODEL = "BAAI/bge-base-en-v1.5"
DEFAULT_BGE_SEMANTIC_THRESHOLD = 0.35


@dataclass(slots=True)
class BgeSemanticNearMissBackend:
    """Semantic near-miss reranker backed by an optional BGE sentence model.

    ``model`` may be injected by tests or host applications. When omitted, the
    backend lazily imports ``sentence_transformers.SentenceTransformer`` and loads
    ``model_name`` on first use. The backend reranks only review hints that the
    deterministic heuristic already marked for semantic escalation, while keeping
    non-escalated high-confidence heuristic suggestions unchanged.
    """

    model_name: str = DEFAULT_BGE_BASE_MODEL
    min_semantic_score: float = DEFAULT_BGE_SEMANTIC_THRESHOLD
    only_escalated: bool = True
    model: Any | None = None
    name: str = "bge-base"
    deterministic: bool = False
    _loaded_model: Any | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.model_name = _clean_text(self.model_name, field_name="model_name")
        self.min_semantic_score = float(self.min_semantic_score)
        if not 0.0 <= self.min_semantic_score <= 1.0:
            raise SemanticNearMissError("min_semantic_score must be between 0.0 and 1.0")
        if not isinstance(self.only_escalated, bool):
            raise SemanticNearMissError("only_escalated must be a boolean")
        self.name = _clean_text(self.name, field_name="name")
        if not isinstance(self.deterministic, bool):
            raise SemanticNearMissError("deterministic must be a boolean")

    def rerank(self, request: SemanticNearMissRequest) -> SemanticNearMissResult:
        """Return semantic reranking/filtering metadata for a near-miss request."""
        if not isinstance(request, SemanticNearMissRequest):
            raise SemanticNearMissError("request must be a SemanticNearMissRequest")
        if not request.candidates:
            return SemanticNearMissResult(
                source=SemanticSuggestionSource.SEMANTIC,
                backend_name=self.name,
                applied=False,
                deterministic=self.deterministic,
                candidates=(),
                metadata={
                    "model_name": self.model_name,
                    "reason": "no_candidates",
                    "min_semantic_score": self.min_semantic_score,
                },
            )

        scored_candidates: list[SemanticNearMissCandidate] = []
        encoded_items: list[tuple[int, SemanticNearMissCandidate, str]] = []
        for index, candidate in enumerate(request.candidates):
            if self.only_escalated and not _semantic_escalation_recommended(candidate):
                scored_candidates.append(_with_metadata(candidate, {
                    "semantic_skipped": True,
                    "semantic_skip_reason": "not_escalated",
                }))
                continue
            encoded_items.append((index, candidate, _candidate_text(candidate)))

        if encoded_items:
            model = self._model()
            texts = [_query_text(request), *(item[2] for item in encoded_items)]
            vectors = _encode_texts(model, texts)
            query_vector = vectors[0]
            candidate_vectors = vectors[1:]
            for (_, candidate, _), vector in zip(encoded_items, candidate_vectors):
                score = round(_cosine_similarity(query_vector, vector), 4)
                if score < self.min_semantic_score:
                    continue
                scored_candidates.append(_with_metadata(candidate, {
                    "semantic_score": score,
                    "semantic_model": self.model_name,
                    "semantic_backend": self.name,
                    "semantic_threshold": self.min_semantic_score,
                    "semantic_skipped": False,
                }))

        ordered = tuple(sorted(scored_candidates, key=_semantic_sort_key))
        return SemanticNearMissResult(
            source=SemanticSuggestionSource.SEMANTIC,
            backend_name=self.name,
            applied=bool(encoded_items),
            deterministic=self.deterministic,
            candidates=ordered,
            metadata={
                "model_name": self.model_name,
                "min_semantic_score": self.min_semantic_score,
                "only_escalated": self.only_escalated,
                "input_candidate_count": len(request.candidates),
                "scored_candidate_count": len(encoded_items),
                "output_candidate_count": len(ordered),
            },
        )

    def _model(self) -> Any:
        if self.model is not None:
            return self.model
        if self._loaded_model is not None:
            return self._loaded_model
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]
        except ImportError as exc:
            raise SemanticNearMissError(
                "semantic near-miss requires sentence-transformers; install sentence-transformers>=2.6,<4.0"
            ) from exc
        self._loaded_model = SentenceTransformer(self.model_name)
        return self._loaded_model


def _semantic_escalation_recommended(candidate: SemanticNearMissCandidate) -> bool:
    semantic = candidate.metadata.get("semantic_escalation")
    return isinstance(semantic, Mapping) and semantic.get("recommended") is True


def _with_metadata(candidate: SemanticNearMissCandidate, metadata: Mapping[str, Any]) -> SemanticNearMissCandidate:
    merged = dict(candidate.metadata)
    merged.update(metadata)
    return SemanticNearMissCandidate(
        target_term_id=candidate.target_term_id,
        target_canonical=candidate.target_canonical,
        matched_surface=candidate.matched_surface,
        confidence=candidate.confidence,
        reasons=candidate.reasons,
        metadata=merged,
    )


def _query_text(request: SemanticNearMissRequest) -> str:
    fragments = " ".join(request.query_fragments)
    if fragments and fragments != request.normalized_surface:
        return f"identifier: {request.normalized_surface}\nfragments: {fragments}"
    return f"identifier: {request.normalized_surface}"


def _candidate_text(candidate: SemanticNearMissCandidate) -> str:
    target_fragments = candidate.metadata.get("target_fragments")
    if isinstance(target_fragments, Sequence) and not isinstance(target_fragments, (str, bytes)):
        fragment_label = " ".join(str(fragment) for fragment in target_fragments if str(fragment).strip())
    else:
        fragment_label = ""
    parts = [f"canonical: {candidate.target_canonical}", f"surface: {candidate.matched_surface}"]
    if fragment_label:
        parts.append(f"fragments: {fragment_label}")
    return "\n".join(parts)


def _encode_texts(model: Any, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
    try:
        raw_vectors = model.encode(
            list(texts),
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
    except TypeError:
        raw_vectors = model.encode(list(texts))
    vectors = tuple(_normalize_vector(_coerce_vector(vector)) for vector in raw_vectors)
    if len(vectors) != len(texts):
        raise SemanticNearMissError("semantic model returned an unexpected number of vectors")
    return vectors


def _coerce_vector(value: Any) -> tuple[float, ...]:
    if hasattr(value, "tolist"):
        value = value.tolist()
    try:
        vector = tuple(float(item) for item in value)
    except TypeError as exc:
        raise SemanticNearMissError("semantic model returned a non-vector item") from exc
    if not vector:
        raise SemanticNearMissError("semantic model returned an empty vector")
    return vector


def _normalize_vector(vector: tuple[float, ...]) -> tuple[float, ...]:
    norm = math.sqrt(sum(item * item for item in vector))
    if norm <= 0.0:
        raise SemanticNearMissError("semantic model returned a zero vector")
    return tuple(item / norm for item in vector)


def _cosine_similarity(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) != len(right):
        raise SemanticNearMissError("semantic vectors must have the same dimension")
    score = sum(a * b for a, b in zip(left, right))
    return max(0.0, min(1.0, score))


def _semantic_sort_key(candidate: SemanticNearMissCandidate) -> tuple[float, float, str, str]:
    score = candidate.metadata.get("semantic_score")
    semantic_score = float(score) if isinstance(score, (int, float)) else -1.0
    return (-semantic_score, -candidate.confidence, candidate.target_term_id, candidate.matched_surface)


def _clean_text(value: str, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise SemanticNearMissError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise SemanticNearMissError(f"{field_name} must not be empty")
    return cleaned


__all__ = [
    "BgeSemanticNearMissBackend",
    "DEFAULT_BGE_BASE_MODEL",
    "DEFAULT_BGE_SEMANTIC_THRESHOLD",
]
