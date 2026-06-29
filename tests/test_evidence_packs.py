from __future__ import annotations

import json
from pathlib import Path

from agent_lexicon import (
    EvidenceSnippetKind,
    build_evidence_packs,
    discover_scout_candidates,
    ingest_local_paths,
)
from agent_lexicon.cli import main


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_build_evidence_packs_collects_positive_and_negative_snippets(tmp_path: Path) -> None:
    _write(
        tmp_path / "docs" / "billing.md",
        """
# Billing operations

Use `billing.update_credit_limit` when the request is scoped to credit limit changes.
Credit limit review happens before billing approval.
""".strip(),
    )
    ingest_report = ingest_local_paths([tmp_path / "docs"], root=tmp_path)
    candidate_report = discover_scout_candidates(ingest_report.documents, min_score=0.2, max_candidates=10)
    candidate = next(item for item in candidate_report.candidates if item.surface == "billing.update_credit_limit")

    evidence_report = build_evidence_packs(
        ingest_report.documents,
        [candidate],
        context_lines=0,
        max_positive_snippets=2,
        max_negative_snippets=2,
    )

    assert evidence_report.pack_count == 1
    pack = evidence_report.packs[0]
    assert pack.surface == "billing.update_credit_limit"
    assert pack.positive_count == 1
    assert pack.negative_count == 1
    assert pack.positive_snippets[0].kind == EvidenceSnippetKind.POSITIVE
    assert pack.positive_snippets[0].start_line == 3
    assert "billing.update_credit_limit" in pack.positive_snippets[0].text
    assert pack.negative_snippets[0].kind == EvidenceSnippetKind.NEGATIVE
    assert pack.negative_snippets[0].reason == "partial_token_overlap_without_surface"
    assert "Credit limit review" in pack.negative_snippets[0].text


def test_evidence_pack_report_is_json_serializable(tmp_path: Path) -> None:
    _write(
        tmp_path / "docs" / "billing.md",
        "BillingGateway calls `billing.update_credit_limit` for credit limit changes.\n",
    )
    ingest_report = ingest_local_paths([tmp_path / "docs"], root=tmp_path)
    candidate_report = discover_scout_candidates(ingest_report.documents, min_score=0.2, max_candidates=5)
    evidence_report = build_evidence_packs(ingest_report.documents, candidate_report.candidates, context_lines=0)

    payload = evidence_report.to_dict()
    encoded = json.dumps(payload, sort_keys=True)

    assert "billing.update_credit_limit" in encoded
    assert payload["pack_count"] == len(candidate_report.candidates)


def test_cli_build_evidence_reports_summary(tmp_path: Path, capsys) -> None:
    _write(
        tmp_path / "docs" / "billing.md",
        "Use `billing.update_credit_limit` for credit limit changes.\nCredit limit review happens first.\n",
    )

    exit_code = main([
        "build-evidence",
        str(tmp_path / "docs"),
        "--root",
        str(tmp_path),
        "--max-candidates",
        "5",
        "--context-lines",
        "0",
    ])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Evidence packs:" in captured.out
    assert "billing.update_credit_limit" in captured.out


def test_cli_build_evidence_can_emit_json(tmp_path: Path, capsys) -> None:
    _write(tmp_path / "docs" / "billing.md", "BillingGateway uses `billing.update_credit_limit`.\n")

    exit_code = main([
        "build-evidence",
        str(tmp_path / "docs"),
        "--root",
        str(tmp_path),
        "--json",
    ])
    captured = capsys.readouterr()

    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["document_count"] == 1
    assert payload["pack_count"] >= 1
