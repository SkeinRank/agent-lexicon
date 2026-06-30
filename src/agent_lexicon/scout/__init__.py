"""Local scout helpers for Agent Lexicon."""

from __future__ import annotations

from .oov import (
    DEFAULT_OOV_TOKENIZER,
    OovScorerError,
    OovScoreResult,
    ProxyOovScorer,
    TokenizerOovScorer,
    build_oov_scorer,
)
from .quality import (
    CandidateCluster,
    CandidatePriority,
    CandidateQualityError,
    CandidateQualitySignals,
    candidate_cluster_key,
    cluster_surface_records,
    compute_candidate_quality,
    surface_fragments,
)
from .candidates import (
    CandidateSurfaceKind,
    ScoutCandidate,
    ScoutCandidateError,
    ScoutCandidateOccurrence,
    ScoutCandidateReport,
    discover_scout_candidates,
    existing_surfaces_from_lexicon,
)

from .migrations import (
    CanonicalMigrationCandidate,
    CanonicalMigrationError,
    CanonicalMigrationReport,
    discover_canonical_migration_candidates,
)
from .evidence import (
    EvidencePack,
    EvidencePackError,
    EvidencePackReport,
    EvidenceSnippet,
    EvidenceSnippetKind,
    build_evidence_packs,
)
from .metrics import (
    ScoutQualityCandidateSummary,
    ScoutQualityReport,
    ScoutQualityReportError,
    build_scout_quality_report,
    build_scout_quality_report_from_review_items,
)

__all__ = [
    "DEFAULT_OOV_TOKENIZER",
    "OovScorerError",
    "OovScoreResult",
    "ProxyOovScorer",
    "TokenizerOovScorer",
    "build_oov_scorer",
    "CandidateCluster",
    "CandidatePriority",
    "CandidateQualityError",
    "CandidateQualitySignals",
    "candidate_cluster_key",
    "cluster_surface_records",
    "compute_candidate_quality",
    "surface_fragments",
    "discover_canonical_migration_candidates",
    "CanonicalMigrationReport",
    "CanonicalMigrationError",
    "CanonicalMigrationCandidate",
    "CandidateSurfaceKind",
    "EvidencePack",
    "EvidencePackError",
    "EvidencePackReport",
    "EvidenceSnippet",
    "EvidenceSnippetKind",
    "ScoutQualityCandidateSummary",
    "ScoutQualityReport",
    "ScoutQualityReportError",
    "build_scout_quality_report",
    "build_scout_quality_report_from_review_items",
    "ScoutCandidate",
    "ScoutCandidateError",
    "ScoutCandidateOccurrence",
    "ScoutCandidateReport",
    "build_evidence_packs",
    "discover_scout_candidates",
    "existing_surfaces_from_lexicon",
]
