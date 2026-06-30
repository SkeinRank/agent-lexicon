"""Runtime term resolution and ambiguity detection for Agent Lexicon."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Mapping

from agent_lexicon.text import normalize_text_for_matching

from .matcher import SurfaceKind, SurfaceMatch, SurfaceMatcher, _select_long_non_overlapping, build_surface_matcher
from .models import Lexicon, ResolutionAction, ResolutionCandidate, ResolutionDecision, ResolutionMatch, ResolutionStatus, Term


@dataclass(frozen=True, slots=True)
class LexiconResolver:
    """Resolve known terminology in text using a loaded lexicon.

    The resolver keeps all behavior deterministic. It uses the surface matcher to
    find known canonical terms and aliases, removes shorter overlapping matches,
    preserves same-span ambiguity, and returns a structured decision for agents
    or command line workflows.
    """

    lexicon: Lexicon
    matcher: SurfaceMatcher

    @classmethod
    def from_lexicon(cls, lexicon: Lexicon, *, include_deprecated: bool = True) -> "LexiconResolver":
        """Build a resolver from a loaded lexicon."""
        return cls(lexicon=lexicon, matcher=build_surface_matcher(lexicon, include_deprecated=include_deprecated))

    def resolve(
        self,
        text: str,
        *,
        scopes: Iterable[str] | None = None,
        include_deprecated: bool = True,
        include_near_misses: bool = True,
        near_miss_max_suggestions: int = 3,
        near_miss_min_confidence: float = 0.42,
    ) -> ResolutionDecision:
        """Resolve terminology in text and classify the result.

        Returns ``unknown`` when no known surfaces are found, ``resolved`` when
        all selected matches point to a single canonical term, and ``ambiguous``
        when the text can refer to more than one canonical term.
        """
        if not isinstance(text, str):
            raise TypeError("text must be a string")

        normalization = normalize_text_for_matching(text)
        metadata = _resolution_metadata(normalization)
        raw_matches = self.matcher.match(
            text,
            scopes=scopes,
            include_deprecated=include_deprecated,
            longest_only=False,
        )
        selected_matches = _select_resolution_matches(raw_matches)
        resolution_matches = tuple(_to_resolution_match(match) for match in selected_matches)

        if not selected_matches:
            if include_near_misses:
                metadata.update(
                    _near_miss_metadata(
                        self.lexicon,
                        text,
                        scopes=scopes,
                        include_deprecated=include_deprecated,
                        max_suggestions_per_surface=near_miss_max_suggestions,
                        min_confidence=near_miss_min_confidence,
                    )
                )
            return ResolutionDecision(
                text=text,
                status=ResolutionStatus.UNKNOWN,
                action=ResolutionAction.NO_MATCH,
                candidates=(),
                matches=(),
                message="No known terminology surfaces were found.",
                metadata=metadata,
            )

        candidates = _build_candidates(self.lexicon, selected_matches)
        if len(candidates) == 1:
            term = candidates[0]
            return ResolutionDecision(
                text=text,
                status=ResolutionStatus.RESOLVED,
                action=ResolutionAction.USE_TERMS,
                candidates=candidates,
                matches=resolution_matches,
                message=f"Resolved to {term.term_id}.",
                metadata=metadata,
            )

        return ResolutionDecision(
            text=text,
            status=ResolutionStatus.AMBIGUOUS,
            action=ResolutionAction.ASK_CLARIFICATION,
            candidates=candidates,
            matches=resolution_matches,
            message=f"Found {len(candidates)} possible canonical terms.",
            metadata=metadata,
        )


def resolve_text(
    lexicon: Lexicon,
    text: str,
    *,
    scopes: Iterable[str] | None = None,
    include_deprecated: bool = True,
    include_near_misses: bool = True,
    near_miss_max_suggestions: int = 3,
    near_miss_min_confidence: float = 0.42,
    use_cache: bool = True,
) -> ResolutionDecision:
    """Convenience helper that resolves text against a lexicon.

    By default the helper reuses a process-wide compiled resolver cache. Direct
    ``LexiconResolver.from_lexicon(...)`` construction remains available when a
    caller wants an isolated resolver instance.
    """
    if use_cache:
        from .cache import get_cached_resolver

        resolver = get_cached_resolver(lexicon, include_deprecated=include_deprecated)
    else:
        resolver = LexiconResolver.from_lexicon(lexicon, include_deprecated=include_deprecated)
    return resolver.resolve(
        text,
        scopes=scopes,
        include_deprecated=include_deprecated,
        include_near_misses=include_near_misses,
        near_miss_max_suggestions=near_miss_max_suggestions,
        near_miss_min_confidence=near_miss_min_confidence,
    )



def _near_miss_metadata(
    lexicon: Lexicon,
    text: str,
    *,
    scopes: Iterable[str] | None,
    include_deprecated: bool,
    max_suggestions_per_surface: int,
    min_confidence: float,
) -> dict[str, object]:
    from agent_lexicon.scout.near_miss import (
        discover_unknown_identifier_surfaces,
        suggest_near_misses_for_text,
    )

    unknown_surfaces = discover_unknown_identifier_surfaces(text)
    if not unknown_surfaces:
        return {}
    reports = suggest_near_misses_for_text(
        lexicon,
        text,
        scopes=scopes,
        include_deprecated=include_deprecated,
        max_suggestions_per_surface=max_suggestions_per_surface,
        min_confidence=min_confidence,
    )
    metadata: dict[str, object] = {"unknown_identifier_surfaces": list(unknown_surfaces)}
    if reports:
        metadata["near_miss_suggestions"] = [report.to_dict() for report in reports]
    return metadata


def _select_resolution_matches(matches: tuple[SurfaceMatch, ...]) -> tuple[SurfaceMatch, ...]:
    """Keep long non-overlapping spans while preserving same-span ambiguity."""
    return _select_long_non_overlapping(matches, preserve_same_span=True)


def _resolution_metadata(normalization) -> dict[str, object]:
    metadata = normalization.metadata()
    if not metadata["unicode_normalized"] and not metadata["unicode_findings"]:
        return {}
    return metadata


def _build_candidates(lexicon: Lexicon, matches: tuple[SurfaceMatch, ...]) -> tuple[ResolutionCandidate, ...]:
    by_term_id: dict[str, list[SurfaceMatch]] = defaultdict(list)
    for match in matches:
        by_term_id[match.term_id].append(match)

    candidates: list[ResolutionCandidate] = []
    for term_id, term_matches in by_term_id.items():
        term = lexicon.get_term(term_id)
        if term is None:
            continue
        candidates.append(_candidate_from_term(term, term_matches))

    return tuple(sorted(candidates, key=lambda candidate: (-candidate.score, candidate.term_id)))


def _candidate_from_term(term: Term, matches: list[SurfaceMatch]) -> ResolutionCandidate:
    matched_surfaces = tuple(dict.fromkeys(match.matched_text for match in matches))
    matched_scope_values: list[str] = []
    for match in matches:
        matched_scope_values.extend(match.scopes)
    matched_scopes = tuple(dict.fromkeys(matched_scope_values)) or term.scopes
    canonical_hits = sum(1 for match in matches if match.kind == SurfaceKind.CANONICAL)
    total_length = sum(match.length for match in matches)
    score = total_length + (canonical_hits * 10)

    return ResolutionCandidate(
        term_id=term.id,
        canonical=term.canonical,
        description=term.description,
        scopes=matched_scopes,
        tags=term.tags,
        matched_surfaces=matched_surfaces,
        match_count=len(matches),
        evidence_count=len(term.evidence),
        deprecated=term.deprecated or any(match.deprecated for match in matches),
        score=float(score),
    )


def _to_resolution_match(match: SurfaceMatch) -> ResolutionMatch:
    return ResolutionMatch(
        term_id=match.term_id,
        surface=match.surface,
        matched_text=match.matched_text,
        start=match.start,
        end=match.end,
        kind=match.kind.value,
        scopes=match.scopes,
        deprecated=match.deprecated,
    )


__all__ = [
    "LexiconResolver",
    "resolve_text",
]
