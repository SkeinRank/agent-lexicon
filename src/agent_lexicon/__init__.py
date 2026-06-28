"""Agent Lexicon: shared terminology memory for AI agents."""

from __future__ import annotations

from .core import (
    AgentLexiconLoadError,
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
    lexicon_from_dict,
    load_lexicon,
    loads_lexicon,
)

__all__ = [
    "__version__",
    "about",
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

__version__ = "0.0.1"


def about() -> str:
    """Return a short package description."""
    return "Agent Lexicon: shared terminology memory for AI agents."
