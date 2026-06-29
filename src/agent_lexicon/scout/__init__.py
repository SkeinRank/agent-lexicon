"""Local scout helpers for Agent Lexicon."""

from __future__ import annotations

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

__all__ = [
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
    "ScoutCandidate",
    "ScoutCandidateError",
    "ScoutCandidateOccurrence",
    "ScoutCandidateReport",
    "build_evidence_packs",
    "discover_scout_candidates",
    "existing_surfaces_from_lexicon",
]
