"""Local scout candidate discovery for Agent Lexicon.

The scout layer scans ingested local documents and proposes reviewable surfaces
that look like project terminology. It is deterministic and dependency-free: no
LLM, embedding model, or search backend is required for the first discovery
pass.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from agent_lexicon.core import Lexicon
from agent_lexicon.ingest import IngestDocument


class ScoutCandidateError(ValueError):
    """Raised when candidate discovery receives invalid input or options."""


class CandidateSurfaceKind(str, Enum):
    """Best-effort surface category for a discovered candidate."""

    PHRASE = "phrase"
    IDENTIFIER = "identifier"
    ACRONYM = "acronym"
    CODE = "code"


@dataclass(frozen=True, slots=True)
class ScoutCandidateOccurrence:
    """One document occurrence supporting a candidate surface."""

    document_path: str
    line_number: int
    line_text: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "document_path", _clean_text(self.document_path, field_name="document_path"))
        if self.line_number < 1:
            raise ScoutCandidateError("line_number must be greater than 0")
        object.__setattr__(self, "line_text", _clean_text(self.line_text, field_name="line_text"))

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable occurrence representation."""
        return {
            "document_path": self.document_path,
            "line_number": self.line_number,
            "line_text": self.line_text,
        }


@dataclass(frozen=True, slots=True)
class ScoutCandidate:
    """A terminology candidate discovered from local documents."""

    surface: str
    normalized_surface: str
    kind: CandidateSurfaceKind
    score: float
    jargon_score: float
    background_penalty: float
    occurrence_count: int
    document_count: int
    occurrences: tuple[ScoutCandidateOccurrence, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "surface", _clean_text(self.surface, field_name="surface"))
        object.__setattr__(self, "normalized_surface", _clean_text(self.normalized_surface, field_name="normalized_surface"))
        object.__setattr__(self, "kind", CandidateSurfaceKind(self.kind.value if isinstance(self.kind, CandidateSurfaceKind) else str(self.kind)))
        object.__setattr__(self, "score", _bounded_float(self.score, field_name="score"))
        object.__setattr__(self, "jargon_score", _bounded_float(self.jargon_score, field_name="jargon_score"))
        object.__setattr__(self, "background_penalty", _bounded_float(self.background_penalty, field_name="background_penalty"))
        if self.occurrence_count < 1:
            raise ScoutCandidateError("occurrence_count must be greater than 0")
        if self.document_count < 1:
            raise ScoutCandidateError("document_count must be greater than 0")
        if not isinstance(self.occurrences, tuple):
            object.__setattr__(self, "occurrences", tuple(self.occurrences))
        for occurrence in self.occurrences:
            if not isinstance(occurrence, ScoutCandidateOccurrence):
                raise ScoutCandidateError("occurrences must contain ScoutCandidateOccurrence objects")
        if not isinstance(self.metadata, Mapping):
            raise ScoutCandidateError("metadata must be a mapping")
        object.__setattr__(self, "metadata", {str(key): value for key, value in self.metadata.items()})

    @property
    def score_breakdown(self) -> Mapping[str, Any]:
        """Return the deterministic scoring components for this candidate."""
        return {
            "score": self.score,
            "jargon_score": self.jargon_score,
            "background_penalty": self.background_penalty,
            "occurrence_count": self.occurrence_count,
            "document_count": self.document_count,
            **dict(self.metadata.get("score_breakdown", {})),
        }

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable candidate representation."""
        return {
            "surface": self.surface,
            "normalized_surface": self.normalized_surface,
            "kind": self.kind.value,
            "score": self.score,
            "jargon_score": self.jargon_score,
            "background_penalty": self.background_penalty,
            "occurrence_count": self.occurrence_count,
            "document_count": self.document_count,
            "occurrences": [occurrence.to_dict() for occurrence in self.occurrences],
            "metadata": dict(self.metadata),
        }

    def to_json_line(self) -> str:
        """Return this candidate as one JSONL row."""
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True, slots=True)
class ScoutCandidateReport:
    """Result returned by local candidate discovery."""

    candidates: tuple[ScoutCandidate, ...]
    document_count: int
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.candidates, tuple):
            object.__setattr__(self, "candidates", tuple(self.candidates))
        for candidate in self.candidates:
            if not isinstance(candidate, ScoutCandidate):
                raise ScoutCandidateError("candidates must contain ScoutCandidate objects")
        if self.document_count < 0:
            raise ScoutCandidateError("document_count must be greater than or equal to 0")
        if not isinstance(self.metadata, Mapping):
            raise ScoutCandidateError("metadata must be a mapping")
        object.__setattr__(self, "metadata", {str(key): value for key, value in self.metadata.items()})

    @property
    def candidate_count(self) -> int:
        """Return the number of discovered candidates."""
        return len(self.candidates)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable report representation."""
        return {
            "candidate_count": self.candidate_count,
            "document_count": self.document_count,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "metadata": dict(self.metadata),
        }


_IDENTIFIER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(?:"
    r"[A-Za-z][A-Za-z0-9]*(?:[._:/-][A-Za-z0-9]+)+"
    r"|[A-Za-z]+_[A-Za-z0-9_]+"
    r"|[A-Z][A-Z0-9]{2,}"
    r"|[A-Za-z]+[A-Z][A-Za-z0-9]*"
    r")"
    r"(?![A-Za-z0-9_])"
)
_BACKTICK_PATTERN = re.compile(r"`([^`\n]{2,80})`")
_WORD_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9]+(?:'[A-Za-z0-9]+)?")

_BACKGROUND_TERMS = {
    "a",
    "about",
    "add",
    "agent",
    "all",
    "an",
    "and",
    "api",
    "are",
    "as",
    "at",
    "be",
    "before",
    "build",
    "call",
    "can",
    "check",
    "class",
    "code",
    "command",
    "config",
    "current",
    "data",
    "default",
    "demo",
    "directory",
    "document",
    "error",
    "example",
    "file",
    "for",
    "from",
    "function",
    "future",
    "get",
    "has",
    "help",
    "if",
    "in",
    "input",
    "into",
    "is",
    "it",
    "json",
    "line",
    "list",
    "load",
    "local",
    "make",
    "module",
    "new",
    "no",
    "not",
    "object",
    "of",
    "on",
    "one",
    "or",
    "output",
    "path",
    "print",
    "project",
    "python",
    "read",
    "report",
    "request",
    "response",
    "return",
    "root",
    "run",
    "service",
    "set",
    "source",
    "string",
    "system",
    "test",
    "text",
    "that",
    "the",
    "this",
    "to",
    "tool",
    "type",
    "use",
    "used",
    "user",
    "value",
    "with",
    "agents",
    "should",
    "when",
    "clearly",
    "refers",
    "scoped",
    "means",
    "tooling",
    "wrapper",
    "wrappers",
    "runtime",
    "supports",
    "will",
    "would",
    "could",
    "workflow",
    "yaml",
}

_DOMAIN_HINTS = {
    "alias",
    "billing",
    "canonical",
    "cap",
    "candidate",
    "credit",
    "customer",
    "guard",
    "jargon",
    "lexicon",
    "limit",
    "proposal",
    "resolver",
    "scope",
    "scout",
    "surface",
    "term",
    "terminology",
}


def discover_scout_candidates(
    documents: Iterable[IngestDocument],
    *,
    existing_surfaces: Iterable[str] | None = None,
    min_score: float = 0.25,
    max_candidates: int = 50,
    max_occurrences_per_candidate: int = 5,
) -> ScoutCandidateReport:
    """Discover terminology candidates from ingested local documents.

    The scorer favors internal-looking surfaces such as code identifiers,
    acronyms, backticked names, and domain phrases. It penalizes broad generic
    surfaces so common project words do not dominate the candidate list.
    """
    if min_score < 0 or min_score > 1:
        raise ScoutCandidateError("min_score must be between 0 and 1")
    if max_candidates < 1:
        raise ScoutCandidateError("max_candidates must be greater than 0")
    if max_occurrences_per_candidate < 1:
        raise ScoutCandidateError("max_occurrences_per_candidate must be greater than 0")

    document_tuple = tuple(documents)
    for document in document_tuple:
        if not isinstance(document, IngestDocument):
            raise ScoutCandidateError("documents must contain IngestDocument objects")

    known_surfaces = {_normalize_surface(surface) for surface in existing_surfaces or () if str(surface).strip()}
    records: dict[str, _CandidateAccumulator] = {}

    for document in document_tuple:
        for line_number, raw_line in enumerate(document.text.splitlines(), start=1):
            line = raw_line.strip()
            if not line:
                continue
            for surface, kind in _extract_candidate_surfaces(line):
                normalized = _normalize_surface(surface)
                if normalized in known_surfaces:
                    continue
                if _is_rejectable_surface(surface, normalized):
                    continue
                accumulator = records.setdefault(
                    normalized,
                    _CandidateAccumulator(surface=surface, normalized_surface=normalized, kind=kind),
                )
                accumulator.add(document.relative_path, line_number, line)

    candidates: list[ScoutCandidate] = []
    total_documents = len(document_tuple)
    for accumulator in records.values():
        candidate = _score_candidate(
            accumulator,
            total_documents=total_documents,
            max_occurrences_per_candidate=max_occurrences_per_candidate,
        )
        if candidate.score >= min_score:
            candidates.append(candidate)

    candidates.sort(
        key=lambda candidate: (
            -candidate.score,
            -candidate.jargon_score,
            -candidate.document_count,
            -candidate.occurrence_count,
            candidate.normalized_surface,
        )
    )
    selected = tuple(candidates[:max_candidates])
    return ScoutCandidateReport(
        candidates=selected,
        document_count=total_documents,
        metadata={
            "min_score": min_score,
            "max_candidates": max_candidates,
            "max_occurrences_per_candidate": max_occurrences_per_candidate,
            "existing_surface_count": len(known_surfaces),
        },
    )


def existing_surfaces_from_lexicon(lexicon: Lexicon) -> tuple[str, ...]:
    """Return canonical and alias surfaces already present in a lexicon."""
    surfaces: list[str] = []
    for term in lexicon.terms:
        surfaces.append(term.canonical)
        for alias in term.aliases:
            surfaces.append(alias.surface)
    return tuple(surfaces)


@dataclass(slots=True)
class _CandidateAccumulator:
    surface: str
    normalized_surface: str
    kind: CandidateSurfaceKind
    occurrence_count: int = 0
    documents: set[str] = field(default_factory=set)
    occurrences: list[ScoutCandidateOccurrence] = field(default_factory=list)

    def add(self, document_path: str, line_number: int, line_text: str) -> None:
        self.occurrence_count += 1
        self.documents.add(document_path)
        if len(self.occurrences) < 20:
            self.occurrences.append(
                ScoutCandidateOccurrence(
                    document_path=document_path,
                    line_number=line_number,
                    line_text=_compact_line(line_text),
                )
            )


def _extract_candidate_surfaces(line: str) -> tuple[tuple[str, CandidateSurfaceKind], ...]:
    surfaces: dict[str, tuple[str, CandidateSurfaceKind]] = {}

    for match in _BACKTICK_PATTERN.finditer(line):
        surface = _clean_surface(match.group(1))
        if surface:
            kind = _classify_surface(surface, from_code=True)
            surfaces[_normalize_surface(surface)] = (surface, kind)

    for match in _IDENTIFIER_PATTERN.finditer(line):
        surface = _clean_surface(match.group(0))
        if surface:
            kind = _classify_surface(surface, from_code=True)
            surfaces.setdefault(_normalize_surface(surface), (surface, kind))

    words = [match.group(0) for match in _WORD_PATTERN.finditer(line)]
    for length in (3, 2):
        for index in range(0, max(len(words) - length + 1, 0)):
            phrase_words = words[index : index + length]
            surface = " ".join(phrase_words)
            normalized = _normalize_surface(surface)
            if normalized in surfaces:
                continue
            if _is_promising_phrase(phrase_words):
                surfaces[normalized] = (surface.lower(), CandidateSurfaceKind.PHRASE)

    return tuple(surfaces.values())


def _classify_surface(surface: str, *, from_code: bool) -> CandidateSurfaceKind:
    if re.fullmatch(r"[A-Z][A-Z0-9]{2,}", surface):
        return CandidateSurfaceKind.ACRONYM
    if any(separator in surface for separator in ("_", "-", ".", ":", "/")) or re.search(r"[a-z][A-Z]", surface):
        return CandidateSurfaceKind.IDENTIFIER
    if from_code:
        return CandidateSurfaceKind.CODE
    return CandidateSurfaceKind.PHRASE


def _is_promising_phrase(words: Sequence[str]) -> bool:
    normalized_words = [word.casefold() for word in words]
    if any(len(word) < 3 for word in normalized_words):
        return False
    if normalized_words[0] in _BACKGROUND_TERMS or normalized_words[-1] in _BACKGROUND_TERMS:
        return False
    background_count = sum(1 for word in normalized_words if word in _BACKGROUND_TERMS)
    if background_count >= len(normalized_words):
        return False
    if any(word in _DOMAIN_HINTS for word in normalized_words):
        return True
    return background_count <= 1 and any(len(word) >= 7 for word in normalized_words)


def _score_candidate(
    accumulator: _CandidateAccumulator,
    *,
    total_documents: int,
    max_occurrences_per_candidate: int,
) -> ScoutCandidate:
    tokens = _surface_tokens(accumulator.normalized_surface)
    document_count = len(accumulator.documents)
    occurrence_score = min(1.0, math.log1p(accumulator.occurrence_count) / math.log1p(10))
    diversity_score = min(1.0, document_count / max(total_documents, 1))
    length_score = min(1.0, len(tokens) / 3)
    jargon_score = _jargon_score(accumulator.surface, tokens, accumulator.kind)
    background_penalty = _background_penalty(tokens, accumulator.kind)
    raw_score = (0.42 * jargon_score) + (0.24 * occurrence_score) + (0.20 * diversity_score) + (0.14 * length_score) - background_penalty
    score = round(max(0.0, min(1.0, raw_score)), 4)

    occurrences = tuple(accumulator.occurrences[:max_occurrences_per_candidate])
    return ScoutCandidate(
        surface=accumulator.surface,
        normalized_surface=accumulator.normalized_surface,
        kind=accumulator.kind,
        score=score,
        jargon_score=round(jargon_score, 4),
        background_penalty=round(background_penalty, 4),
        occurrence_count=accumulator.occurrence_count,
        document_count=document_count,
        occurrences=occurrences,
        metadata={
            "documents": sorted(accumulator.documents),
            "score_breakdown": {
                "occurrence_score": round(occurrence_score, 4),
                "diversity_score": round(diversity_score, 4),
                "length_score": round(length_score, 4),
            },
        },
    )


def _jargon_score(surface: str, tokens: tuple[str, ...], kind: CandidateSurfaceKind) -> float:
    score = 0.18
    if kind in {CandidateSurfaceKind.IDENTIFIER, CandidateSurfaceKind.ACRONYM, CandidateSurfaceKind.CODE}:
        score += 0.42
    if kind == CandidateSurfaceKind.ACRONYM:
        score += 0.20
    if any(separator in surface for separator in ("_", "-", ".", ":", "/")):
        score += 0.18
    if re.search(r"[a-z][A-Z]", surface):
        score += 0.18
    if any(char.isdigit() for char in surface):
        score += 0.10
    if any(token in _DOMAIN_HINTS for token in tokens):
        score += 0.22
    if len(tokens) >= 2:
        score += 0.16
    uncommon_ratio = sum(1 for token in tokens if token not in _BACKGROUND_TERMS) / max(len(tokens), 1)
    score += 0.20 * uncommon_ratio
    return max(0.0, min(1.0, score))


def _background_penalty(tokens: tuple[str, ...], kind: CandidateSurfaceKind) -> float:
    if kind in {CandidateSurfaceKind.IDENTIFIER, CandidateSurfaceKind.ACRONYM}:
        base = 0.02
    else:
        base = 0.06
    background_ratio = sum(1 for token in tokens if token in _BACKGROUND_TERMS) / max(len(tokens), 1)
    penalty = base + (0.30 * background_ratio)
    if len(tokens) == 1 and tokens[0] not in _DOMAIN_HINTS and kind == CandidateSurfaceKind.PHRASE:
        penalty += 0.20
    return max(0.0, min(1.0, penalty))


def _is_rejectable_surface(surface: str, normalized: str) -> bool:
    if len(surface) < 3 or len(surface) > 80:
        return True
    if normalized in _BACKGROUND_TERMS:
        return True
    tokens = _surface_tokens(normalized)
    if not tokens:
        return True
    if len(tokens) == 1 and tokens[0] in _BACKGROUND_TERMS:
        return True
    if len(tokens) > 6:
        return True
    return False


def _surface_tokens(normalized_surface: str) -> tuple[str, ...]:
    return tuple(token for token in re.split(r"[^a-z0-9]+", normalized_surface.casefold()) if token)


def _normalize_surface(surface: str) -> str:
    return " ".join(surface.strip().casefold().split())


def _clean_surface(surface: str) -> str | None:
    cleaned = surface.strip().strip("'\".,;:()[]{}")
    if not cleaned:
        return None
    if cleaned.startswith("http://") or cleaned.startswith("https://"):
        return None
    return cleaned


def _compact_line(line: str, *, max_chars: int = 220) -> str:
    compact = " ".join(line.split())
    if len(compact) <= max_chars:
        return compact
    return f"{compact[: max_chars - 1]}…"


def _clean_text(value: str, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ScoutCandidateError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ScoutCandidateError(f"{field_name} must not be empty")
    return cleaned


def _bounded_float(value: float, *, field_name: str) -> float:
    if not isinstance(value, (int, float)):
        raise ScoutCandidateError(f"{field_name} must be a number")
    as_float = float(value)
    if as_float < 0 or as_float > 1:
        raise ScoutCandidateError(f"{field_name} must be between 0 and 1")
    return as_float


__all__ = [
    "CandidateSurfaceKind",
    "ScoutCandidate",
    "ScoutCandidateError",
    "ScoutCandidateOccurrence",
    "ScoutCandidateReport",
    "discover_scout_candidates",
    "existing_surfaces_from_lexicon",
]
