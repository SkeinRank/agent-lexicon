"""Canonical migration candidate discovery for deprecated terms.

This module turns deprecated terms into reviewable migration candidates. It is
intentionally deterministic and local-first: explicit replacement metadata is
preferred, and a conservative surface-similarity fallback is used only when no
explicit replacement is declared.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Mapping, Sequence

from agent_lexicon.core import Lexicon, ProposalCandidate, ProposalKind, RiskLevel, Term


class CanonicalMigrationError(ValueError):
    """Raised when canonical migration discovery receives invalid input."""


@dataclass(frozen=True, slots=True)
class CanonicalMigrationCandidate:
    """A candidate migration from a deprecated canonical term to an active term."""

    deprecated_term_id: str
    replacement_term_id: str
    deprecated_canonical: str
    replacement_canonical: str
    confidence: float
    risk: RiskLevel
    rationale: str
    deprecated_aliases: tuple[str, ...] = ()
    surfaces_to_preserve: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "deprecated_term_id", _clean_text(self.deprecated_term_id, field_name="deprecated_term_id"))
        object.__setattr__(self, "replacement_term_id", _clean_text(self.replacement_term_id, field_name="replacement_term_id"))
        if self.deprecated_term_id == self.replacement_term_id:
            raise CanonicalMigrationError("deprecated_term_id and replacement_term_id must be different")
        object.__setattr__(self, "deprecated_canonical", _clean_text(self.deprecated_canonical, field_name="deprecated_canonical"))
        object.__setattr__(self, "replacement_canonical", _clean_text(self.replacement_canonical, field_name="replacement_canonical"))
        object.__setattr__(self, "confidence", _bounded_float(self.confidence, field_name="confidence"))
        object.__setattr__(self, "risk", RiskLevel(self.risk.value if isinstance(self.risk, RiskLevel) else str(self.risk)))
        object.__setattr__(self, "rationale", _clean_text(self.rationale, field_name="rationale"))
        object.__setattr__(self, "deprecated_aliases", tuple(_clean_text(alias, field_name="deprecated_alias") for alias in self.deprecated_aliases))
        object.__setattr__(self, "surfaces_to_preserve", tuple(_clean_text(surface, field_name="surface_to_preserve") for surface in self.surfaces_to_preserve))
        if not isinstance(self.metadata, Mapping):
            raise CanonicalMigrationError("metadata must be a mapping")
        object.__setattr__(self, "metadata", {str(key): value for key, value in self.metadata.items()})

    @property
    def proposal_id(self) -> str:
        """Return a stable proposal id for this migration candidate."""
        return f"proposal.migrate.{self.deprecated_term_id}.to.{self.replacement_term_id}"

    def to_proposal_candidate(self) -> ProposalCandidate:
        """Convert this migration into a core ProposalCandidate."""
        return ProposalCandidate(
            id=self.proposal_id,
            kind=ProposalKind.CANONICAL_MIGRATION,
            surface=self.deprecated_canonical,
            candidate_term_id=self.deprecated_term_id,
            target_term_id=self.replacement_term_id,
            confidence=self.confidence,
            risk=self.risk,
            rationale=self.rationale,
            metadata={
                "deprecated_canonical": self.deprecated_canonical,
                "replacement_canonical": self.replacement_canonical,
                "deprecated_aliases": list(self.deprecated_aliases),
                "surfaces_to_preserve": list(self.surfaces_to_preserve),
                **dict(self.metadata),
            },
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable migration candidate."""
        return {
            "deprecated_term_id": self.deprecated_term_id,
            "replacement_term_id": self.replacement_term_id,
            "deprecated_canonical": self.deprecated_canonical,
            "replacement_canonical": self.replacement_canonical,
            "confidence": self.confidence,
            "risk": self.risk.value,
            "rationale": self.rationale,
            "deprecated_aliases": list(self.deprecated_aliases),
            "surfaces_to_preserve": list(self.surfaces_to_preserve),
            "proposal": self.to_proposal_candidate().to_dict(),
            "metadata": dict(self.metadata),
        }

    def to_json_line(self) -> str:
        """Return this migration candidate as one JSONL row."""
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True, slots=True)
class CanonicalMigrationReport:
    """Result returned by canonical migration discovery."""

    candidates: tuple[CanonicalMigrationCandidate, ...]
    deprecated_term_count: int
    active_term_count: int
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.candidates, tuple):
            object.__setattr__(self, "candidates", tuple(self.candidates))
        for candidate in self.candidates:
            if not isinstance(candidate, CanonicalMigrationCandidate):
                raise CanonicalMigrationError("candidates must contain CanonicalMigrationCandidate objects")
        if self.deprecated_term_count < 0:
            raise CanonicalMigrationError("deprecated_term_count must be greater than or equal to 0")
        if self.active_term_count < 0:
            raise CanonicalMigrationError("active_term_count must be greater than or equal to 0")
        if not isinstance(self.metadata, Mapping):
            raise CanonicalMigrationError("metadata must be a mapping")
        object.__setattr__(self, "metadata", {str(key): value for key, value in self.metadata.items()})

    @property
    def candidate_count(self) -> int:
        """Return the number of migration candidates."""
        return len(self.candidates)

    def to_proposals(self) -> tuple[ProposalCandidate, ...]:
        """Return migration candidates as core proposal objects."""
        return tuple(candidate.to_proposal_candidate() for candidate in self.candidates)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable report."""
        return {
            "candidate_count": self.candidate_count,
            "deprecated_term_count": self.deprecated_term_count,
            "active_term_count": self.active_term_count,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "metadata": dict(self.metadata),
        }


def discover_canonical_migration_candidates(
    lexicon: Lexicon,
    *,
    min_confidence: float = 0.35,
    max_candidates: int = 20,
) -> CanonicalMigrationReport:
    """Discover migration candidates from deprecated terms to active terms.

    Deprecated terms can declare an explicit replacement via term metadata:
    ``replacement_term_id``, ``replaced_by``, or
    ``canonical_replacement_term_id``. If no explicit replacement is present,
    the function uses conservative surface similarity against active terms.
    """
    if not isinstance(lexicon, Lexicon):
        raise CanonicalMigrationError("lexicon must be a Lexicon")
    threshold = _bounded_float(min_confidence, field_name="min_confidence")
    if max_candidates < 1:
        raise CanonicalMigrationError("max_candidates must be greater than 0")

    terms_by_id = {term.id: term for term in lexicon.terms}
    deprecated_terms = tuple(term for term in lexicon.terms if term.deprecated)
    active_terms = tuple(term for term in lexicon.terms if not term.deprecated)
    candidates: list[CanonicalMigrationCandidate] = []

    for deprecated_term in deprecated_terms:
        explicit_replacement_id = _explicit_replacement_id(deprecated_term)
        if explicit_replacement_id is not None:
            replacement = terms_by_id.get(explicit_replacement_id)
            if replacement is not None and not replacement.deprecated:
                candidates.append(
                    _build_candidate(
                        deprecated_term,
                        replacement,
                        confidence=0.95,
                        rationale=(
                            f"Deprecated term {deprecated_term.id!r} declares "
                            f"replacement term {replacement.id!r}."
                        ),
                        match_kind="explicit_replacement",
                    )
                )
            continue

        scored_replacements = [
            (_term_similarity(deprecated_term, active_term), active_term)
            for active_term in active_terms
        ]
        scored_replacements.sort(key=lambda item: (-item[0], item[1].id))
        if not scored_replacements:
            continue
        best_score, best_replacement = scored_replacements[0]
        if best_score < threshold:
            continue
        candidates.append(
            _build_candidate(
                deprecated_term,
                best_replacement,
                confidence=best_score,
                rationale=(
                    f"Deprecated term {deprecated_term.id!r} is similar to "
                    f"active term {best_replacement.id!r}."
                ),
                match_kind="surface_similarity",
            )
        )

    candidates.sort(key=lambda candidate: (-candidate.confidence, candidate.deprecated_term_id, candidate.replacement_term_id))
    limited_candidates = tuple(candidates[:max_candidates])
    return CanonicalMigrationReport(
        candidates=limited_candidates,
        deprecated_term_count=len(deprecated_terms),
        active_term_count=len(active_terms),
        metadata={
            "min_confidence": threshold,
            "max_candidates": max_candidates,
        },
    )


def _build_candidate(
    deprecated_term: Term,
    replacement_term: Term,
    *,
    confidence: float,
    rationale: str,
    match_kind: str,
) -> CanonicalMigrationCandidate:
    deprecated_surfaces = _term_surfaces(deprecated_term)
    replacement_surfaces = {surface.casefold() for surface in _term_surfaces(replacement_term)}
    surfaces_to_preserve = tuple(
        surface for surface in deprecated_surfaces
        if surface.casefold() not in replacement_surfaces
    )
    alias_surfaces = tuple(alias.surface for alias in deprecated_term.aliases)
    return CanonicalMigrationCandidate(
        deprecated_term_id=deprecated_term.id,
        replacement_term_id=replacement_term.id,
        deprecated_canonical=deprecated_term.canonical,
        replacement_canonical=replacement_term.canonical,
        confidence=confidence,
        risk=_migration_risk(deprecated_term, replacement_term, match_kind=match_kind),
        rationale=rationale,
        deprecated_aliases=alias_surfaces,
        surfaces_to_preserve=surfaces_to_preserve,
        metadata={
            "match_kind": match_kind,
            "deprecated_scopes": list(deprecated_term.scopes),
            "replacement_scopes": list(replacement_term.scopes),
            "suggested_actions": [
                "keep deprecated term inactive",
                "preserve deprecated surfaces as aliases on the replacement term after review",
                "update docs and agent prompts to prefer the replacement canonical",
            ],
            "score_breakdown": _score_breakdown(deprecated_term, replacement_term),
        },
    )


def _migration_risk(deprecated_term: Term, replacement_term: Term, *, match_kind: str) -> RiskLevel:
    if match_kind == "explicit_replacement":
        if _scope_overlap(deprecated_term, replacement_term):
            return RiskLevel.LOW
        return RiskLevel.MEDIUM
    if _scope_overlap(deprecated_term, replacement_term):
        return RiskLevel.MEDIUM
    return RiskLevel.HIGH


def _explicit_replacement_id(term: Term) -> str | None:
    for key in ("replacement_term_id", "replaced_by", "canonical_replacement_term_id"):
        value = term.metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _term_similarity(left: Term, right: Term) -> float:
    surface_scores = [
        _surface_similarity(left_surface, right_surface)
        for left_surface in _term_surfaces(left)
        for right_surface in _term_surfaces(right)
    ]
    best_surface_score = max(surface_scores) if surface_scores else 0.0
    scope_bonus = 0.10 if _scope_overlap(left, right) else 0.0
    tag_bonus = 0.05 if set(left.tags) & set(right.tags) else 0.0
    return round(min(1.0, best_surface_score + scope_bonus + tag_bonus), 4)


def _score_breakdown(left: Term, right: Term) -> dict[str, Any]:
    return {
        "surface_similarity": round(max(
            (_surface_similarity(left_surface, right_surface) for left_surface in _term_surfaces(left) for right_surface in _term_surfaces(right)),
            default=0.0,
        ), 4),
        "scope_overlap": _scope_overlap(left, right),
        "tag_overlap": sorted(set(left.tags) & set(right.tags)),
    }


def _surface_similarity(left: str, right: str) -> float:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    left_set = set(left_tokens)
    right_set = set(right_tokens)
    jaccard = len(left_set & right_set) / len(left_set | right_set)
    sequence = SequenceMatcher(None, left.casefold(), right.casefold()).ratio()
    return round((jaccard * 0.65) + (sequence * 0.35), 4)


def _scope_overlap(left: Term, right: Term) -> bool:
    if not left.scopes or not right.scopes:
        return True
    return bool(set(left.scopes) & set(right.scopes))


def _term_surfaces(term: Term) -> tuple[str, ...]:
    return (term.canonical, *(alias.surface for alias in term.aliases))


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[a-z0-9]+", value.casefold()))


def _bounded_float(value: float, *, field_name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise CanonicalMigrationError(f"{field_name} must be a number") from exc
    if not 0.0 <= parsed <= 1.0:
        raise CanonicalMigrationError(f"{field_name} must be between 0.0 and 1.0")
    return parsed


def _clean_text(value: str, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise CanonicalMigrationError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise CanonicalMigrationError(f"{field_name} must not be empty")
    return cleaned


__all__ = [
    "CanonicalMigrationCandidate",
    "CanonicalMigrationError",
    "CanonicalMigrationReport",
    "discover_canonical_migration_candidates",
]
