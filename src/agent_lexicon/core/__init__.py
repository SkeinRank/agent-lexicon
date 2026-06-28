"""Core schema objects for Agent Lexicon."""

from __future__ import annotations

from .loader import AgentLexiconLoadError, lexicon_from_dict, load_lexicon, loads_lexicon
from .matcher import (
    SurfaceEntry,
    SurfaceKind,
    SurfaceMatch,
    SurfaceMatcher,
    build_surface_matcher,
    find_surface_matches,
)
from .models import (
    AgentLexiconModelError,
    Alias,
    EvidenceKind,
    EvidenceSpan,
    Lexicon,
    ProposalCandidate,
    ProposalKind,
    ProposalStatus,
    RiskLevel,
    Scope,
    Term,
)

__all__ = [
    "AgentLexiconLoadError",
    "AgentLexiconModelError",
    "Alias",
    "EvidenceKind",
    "EvidenceSpan",
    "Lexicon",
    "ProposalCandidate",
    "ProposalKind",
    "ProposalStatus",
    "RiskLevel",
    "SurfaceEntry",
    "SurfaceKind",
    "SurfaceMatch",
    "SurfaceMatcher",
    "Scope",
    "Term",
    "lexicon_from_dict",
    "load_lexicon",
    "build_surface_matcher",
    "find_surface_matches",
    "loads_lexicon",
]
