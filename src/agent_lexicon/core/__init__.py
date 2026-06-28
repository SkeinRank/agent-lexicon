"""Core schema objects for Agent Lexicon."""

from __future__ import annotations

from .loader import AgentLexiconLoadError, lexicon_from_dict, load_lexicon, loads_lexicon
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
    "Scope",
    "Term",
    "lexicon_from_dict",
    "load_lexicon",
    "loads_lexicon",
]
