from __future__ import annotations

import json
from pathlib import Path

from agent_lexicon import (
    CandidateSurfaceKind,
    IngestDocument,
    discover_scout_candidates,
    existing_surfaces_from_lexicon,
    ingest_local_paths,
    load_lexicon,
)
from agent_lexicon.cli import main


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_discover_scout_candidates_scores_internal_surfaces(tmp_path: Path) -> None:
    _write(
        tmp_path / "docs" / "billing.md",
        """
# Billing vocabulary

The Billing Gateway owns `billing.update_credit_limit` for account decisions.
CustomerCapFlag controls the account-level credit limit migration.
""".strip(),
    )
    report = ingest_local_paths([tmp_path / "docs"], root=tmp_path)

    candidates = discover_scout_candidates(report.documents, min_score=0.25, max_candidates=10)
    surfaces = {candidate.surface: candidate for candidate in candidates.candidates}

    assert "billing.update_credit_limit" in surfaces
    assert surfaces["billing.update_credit_limit"].kind == CandidateSurfaceKind.IDENTIFIER
    assert surfaces["billing.update_credit_limit"].jargon_score >= 0.8
    assert surfaces["billing.update_credit_limit"].score > surfaces["billing.update_credit_limit"].background_penalty
    assert surfaces["billing.update_credit_limit"].occurrences[0].document_path == "docs/billing.md"


def test_discover_scout_candidates_applies_background_penalties() -> None:
    document = IngestDocument(
        source_path="README.md",
        relative_path="README.md",
        text="The system request response object is used by the local project.\n",
        kind="markdown",
        size_bytes=64,
        line_count=1,
        sha256="a" * 64,
    )

    report = discover_scout_candidates([document], min_score=0.35)

    assert report.candidate_count == 0


def test_existing_surfaces_from_lexicon_filters_known_terms(tmp_path: Path) -> None:
    lexicon = load_lexicon("examples/customer_limits/lexicon.yaml")
    existing_surfaces = existing_surfaces_from_lexicon(lexicon)
    document = IngestDocument(
        source_path=str(tmp_path / "billing.md"),
        relative_path="billing.md",
        text="Customer cap and credit limit are documented surfaces. BillingGateway is new.\n",
        kind="markdown",
        size_bytes=80,
        line_count=1,
        sha256="b" * 64,
    )

    report = discover_scout_candidates([document], existing_surfaces=existing_surfaces, min_score=0.2)
    surfaces = {candidate.normalized_surface for candidate in report.candidates}

    assert "customer cap" not in surfaces
    assert "credit limit" not in surfaces
    assert "billinggateway" in surfaces


def test_cli_discover_candidates_reports_summary(tmp_path: Path, capsys) -> None:
    _write(
        tmp_path / "docs" / "billing.md",
        "BillingGateway calls `billing.update_credit_limit` during credit limit updates.\n",
    )

    exit_code = main([
        "discover-candidates",
        str(tmp_path / "docs"),
        "--root",
        str(tmp_path),
        "--max-candidates",
        "5",
    ])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Candidate discovery:" in captured.out
    assert "billing.update_credit_limit" in captured.out


def test_cli_discover_candidates_can_emit_json(tmp_path: Path, capsys) -> None:
    _write(tmp_path / "docs" / "billing.md", "Use BillingGateway for credit limit updates.\n")

    exit_code = main([
        "discover-candidates",
        str(tmp_path / "docs"),
        "--root",
        str(tmp_path),
        "--json",
    ])
    captured = capsys.readouterr()

    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["document_count"] == 1
    assert payload["candidate_count"] >= 1
