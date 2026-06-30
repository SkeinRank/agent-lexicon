from __future__ import annotations

import json
from pathlib import Path

from agent_lexicon import (
    build_evidence_packs,
    build_scout_quality_report,
    build_scout_quality_report_from_review_items,
    discover_scout_candidates,
    ingest_local_paths,
    init_workspace,
)
from agent_lexicon.cli import main
from agent_lexicon.workspace import open_workspace


def _write_project(root: Path) -> None:
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "billing.md").write_text(
        "PaymentCore owns the customer cap workflow.\n"
        "payments_core emits CustomerCapReview events.\n"
        "Customer cap can also appear outside PaymentCore docs.\n",
        encoding="utf-8",
    )
    (root / "src" / "billing.py").write_text(
        "class CustomerCapReview:\n"
        "    payment_core_id = 'PaymentCore'\n"
        "    tool_name = 'billing.update_credit_limit'\n",
        encoding="utf-8",
    )


def test_build_scout_quality_report_summarizes_candidate_and_evidence_metrics(tmp_path: Path) -> None:
    _write_project(tmp_path)
    ingest = ingest_local_paths([tmp_path / "docs", tmp_path / "src"], root=tmp_path)
    candidates = discover_scout_candidates(ingest.documents, min_score=0.2, max_candidates=10)
    evidence = build_evidence_packs(ingest.documents, candidates.candidates)

    report = build_scout_quality_report(candidates, evidence)

    assert report.candidate_count == candidates.candidate_count
    assert report.important_count >= 1
    assert report.evidence_pack_count == evidence.pack_count
    assert report.evidence_coverage == 1.0
    assert report.code_style_count >= 1
    assert report.high_oov_count >= 1
    assert report.top_candidates
    assert "Scout quality report:" in report.to_text()
    payload = report.to_dict()
    assert payload["important_count"] == report.important_count
    assert payload["top_candidates"][0]["surface"]


def test_build_scout_quality_report_from_workspace_items(tmp_path: Path) -> None:
    _write_project(tmp_path)
    ingest = ingest_local_paths([tmp_path / "docs", tmp_path / "src"], root=tmp_path)
    candidates = discover_scout_candidates(ingest.documents, min_score=0.2, max_candidates=10)
    evidence = build_evidence_packs(ingest.documents, candidates.candidates)
    state = init_workspace(tmp_path)
    state.store_ingest_report(ingest)
    state.store_candidate_report(candidates)
    state.store_evidence_report(evidence)

    items = state.list_review_items(limit=20)
    report = build_scout_quality_report_from_review_items(items, document_count=state.summary().document_count)

    assert report.candidate_count == len(items)
    assert report.evidence_pack_count >= 1
    assert report.document_count == 2
    assert report.top_candidates[0].priority in {"important", "later"}


def test_cli_scan_and_analyze_quality_report(tmp_path: Path, capsys) -> None:
    _write_project(tmp_path)

    assert main(["init", "--root", str(tmp_path)]) == 0
    capsys.readouterr()

    assert main([
        "scan",
        "docs",
        "src",
        "--root",
        str(tmp_path),
        "--min-score",
        "0.2",
        "--max-candidates",
        "10",
        "--quality-report",
    ]) == 0
    scan_output = capsys.readouterr().out
    assert "Scout quality report:" in scan_output
    assert "Review reduction:" in scan_output

    assert main(["analyze", "--root", str(tmp_path), "--quality-report"]) == 0
    analyze_output = capsys.readouterr().out
    assert "Agent Lexicon analyze:" in analyze_output
    assert "Scout quality report:" in analyze_output


def test_cli_discover_candidates_quality_report_and_json_payload(tmp_path: Path, capsys) -> None:
    _write_project(tmp_path)

    assert main([
        "discover-candidates",
        str(tmp_path / "docs"),
        str(tmp_path / "src"),
        "--root",
        str(tmp_path),
        "--min-score",
        "0.2",
        "--max-candidates",
        "10",
        "--quality-report",
    ]) == 0
    output = capsys.readouterr().out
    assert "Candidate discovery:" in output
    assert "Scout quality report:" in output

    assert main([
        "scan",
        "docs",
        "--root",
        str(tmp_path),
        "--json",
        "--min-score",
        "0.2",
        "--max-candidates",
        "5",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["quality_report"]["candidate_count"] == payload["candidate_count"]
