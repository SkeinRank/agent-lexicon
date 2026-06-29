from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_lexicon import (
    Alias,
    CanonicalMigrationError,
    Lexicon,
    ProposalKind,
    RiskLevel,
    Term,
    discover_canonical_migration_candidates,
    loads_lexicon,
)
from agent_lexicon.cli import main
from agent_lexicon.core.loader import AgentLexiconLoadError


def test_discovers_explicit_canonical_migration() -> None:
    lexicon = Lexicon(
        terms=(
            Term(id="billing.credit_limit", canonical="credit limit", scopes=("billing",)),
            Term(
                id="billing.customer_cap",
                canonical="customer cap",
                aliases=(Alias(surface="account cap", term_id="billing.customer_cap"),),
                scopes=("billing",),
                deprecated=True,
                metadata={"replacement_term_id": "billing.credit_limit"},
            ),
        )
    )

    report = discover_canonical_migration_candidates(lexicon)

    assert report.deprecated_term_count == 1
    assert report.active_term_count == 1
    assert report.candidate_count == 1
    candidate = report.candidates[0]
    assert candidate.deprecated_term_id == "billing.customer_cap"
    assert candidate.replacement_term_id == "billing.credit_limit"
    assert candidate.confidence == 0.95
    assert candidate.risk == RiskLevel.LOW
    assert candidate.surfaces_to_preserve == ("customer cap", "account cap")
    proposal = candidate.to_proposal_candidate()
    assert proposal.kind == ProposalKind.CANONICAL_MIGRATION
    assert proposal.candidate_term_id == "billing.customer_cap"
    assert proposal.target_term_id == "billing.credit_limit"


def test_discovers_similarity_based_migration() -> None:
    lexicon = Lexicon(
        terms=(
            Term(id="billing.credit_limit", canonical="credit limit", scopes=("billing",)),
            Term(
                id="billing.old_credit_limit",
                canonical="old credit limit",
                scopes=("billing",),
                deprecated=True,
            ),
        )
    )

    report = discover_canonical_migration_candidates(lexicon, min_confidence=0.2)

    assert report.candidate_count == 1
    candidate = report.candidates[0]
    assert candidate.replacement_term_id == "billing.credit_limit"
    assert candidate.metadata["match_kind"] == "surface_similarity"
    assert candidate.confidence >= 0.2


def test_deprecated_replacement_metadata_is_validated() -> None:
    with pytest.raises(AgentLexiconLoadError, match="unknown replacement term"):
        loads_lexicon(
            """
            version: 1
            terms:
              - id: billing.customer_cap
                canonical: customer cap
                deprecated: true
                metadata:
                  replacement_term_id: billing.credit_limit
            """,
            document_format="yaml",
        )


def test_deprecated_surface_can_overlap_replacement_alias() -> None:
    lexicon = loads_lexicon(
        """
        version: 1
        terms:
          - id: billing.credit_limit
            canonical: credit limit
            aliases:
              - surface: customer cap
          - id: billing.customer_cap
            canonical: customer cap
            deprecated: true
            metadata:
              replacement_term_id: billing.credit_limit
        """,
        document_format="yaml",
    )

    report = discover_canonical_migration_candidates(lexicon)

    assert report.candidate_count == 1
    assert report.candidates[0].surfaces_to_preserve == ()


def test_rejects_invalid_migration_options() -> None:
    lexicon = Lexicon()

    with pytest.raises(CanonicalMigrationError, match="min_confidence"):
        discover_canonical_migration_candidates(lexicon, min_confidence=1.5)
    with pytest.raises(CanonicalMigrationError, match="max_candidates"):
        discover_canonical_migration_candidates(lexicon, max_candidates=0)


def test_cli_discover_migrations_json(tmp_path: Path, capsys) -> None:
    lexicon_path = tmp_path / "lexicon.yaml"
    lexicon_path.write_text(
        """
        version: 1
        terms:
          - id: billing.credit_limit
            canonical: credit limit
          - id: billing.customer_cap
            canonical: customer cap
            deprecated: true
            metadata:
              replacement_term_id: billing.credit_limit
        """,
        encoding="utf-8",
    )

    assert main(["discover-migrations", str(lexicon_path), "--json"]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["candidate_count"] == 1
    assert payload["candidates"][0]["deprecated_term_id"] == "billing.customer_cap"
