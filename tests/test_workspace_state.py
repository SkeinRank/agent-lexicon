from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from agent_lexicon import (
    SCHEMA_VERSION,
    init_workspace,
    open_workspace,
    workspace_path,
    ingest_local_paths,
    discover_scout_candidates,
    build_evidence_packs,
)
from agent_lexicon.cli import main


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_init_workspace_creates_sqlite_database(tmp_path: Path) -> None:
    state = init_workspace(tmp_path)

    assert state.db_path == workspace_path(tmp_path)
    assert state.db_path.exists()

    summary = state.summary()
    assert summary.schema_version == SCHEMA_VERSION
    assert summary.document_count == 0
    assert summary.candidate_count == 0
    assert summary.evidence_pack_count == 0
    assert summary.review_decision_count == 0
    assert summary.snapshot_count == 0


def test_workspace_stores_ingest_candidates_and_evidence(tmp_path: Path) -> None:
    _write(
        tmp_path / "docs" / "billing.md",
        "Use `billing.update_credit_limit` for credit limit changes.\nCredit limit review happens first.\n",
    )

    ingest_report = ingest_local_paths([tmp_path / "docs"], root=tmp_path)
    candidate_report = discover_scout_candidates(ingest_report.documents, min_score=0.2, max_candidates=5)
    evidence_report = build_evidence_packs(ingest_report.documents, candidate_report.candidates, context_lines=0)

    state = init_workspace(tmp_path)
    assert state.store_ingest_report(ingest_report) == 1
    assert state.store_candidate_report(candidate_report) == candidate_report.candidate_count
    assert state.store_evidence_report(evidence_report) == evidence_report.pack_count

    summary = state.summary()
    assert summary.document_count == 1
    assert summary.candidate_count == candidate_report.candidate_count
    assert summary.evidence_pack_count == evidence_report.pack_count
    assert summary.review_decision_count == 0

    with sqlite3.connect(state.db_path) as connection:
        row = connection.execute(
            "SELECT payload_json FROM candidates WHERE normalized_surface = ?",
            ("billing.update_credit_limit",),
        ).fetchone()
    assert row is not None
    assert json.loads(row[0])["surface"] == "billing.update_credit_limit"


def test_workspace_store_is_idempotent_for_same_inputs(tmp_path: Path) -> None:
    _write(tmp_path / "docs" / "billing.md", "BillingGateway uses `billing.update_credit_limit`.\n")
    ingest_report = ingest_local_paths([tmp_path / "docs"], root=tmp_path)
    candidate_report = discover_scout_candidates(ingest_report.documents, min_score=0.2, max_candidates=5)

    state = init_workspace(tmp_path)
    state.store_ingest_report(ingest_report)
    state.store_candidate_report(candidate_report)
    state.store_ingest_report(ingest_report)
    state.store_candidate_report(candidate_report)

    summary = state.summary()
    assert summary.document_count == 1
    assert summary.candidate_count == candidate_report.candidate_count


def test_open_workspace_can_require_existing_database(tmp_path: Path) -> None:
    missing = open_workspace(tmp_path, create=True)
    assert missing.db_path.exists()


def test_cli_workspace_init_status_and_sync(tmp_path: Path, capsys) -> None:
    _write(
        tmp_path / "docs" / "billing.md",
        "Use `billing.update_credit_limit` for credit limit changes.\nCredit limit review happens first.\n",
    )

    assert main(["workspace", "init", "--root", str(tmp_path)]) == 0
    captured = capsys.readouterr()
    assert "Workspace initialized:" in captured.out

    assert main([
        "workspace",
        "sync",
        str(tmp_path / "docs"),
        "--root",
        str(tmp_path),
        "--max-candidates",
        "5",
        "--context-lines",
        "0",
    ]) == 0
    captured = capsys.readouterr()
    assert "Workspace sync:" in captured.out
    assert "documents" in captured.out
    assert "evidence packs saved" in captured.out

    assert main(["workspace", "status", "--root", str(tmp_path)]) == 0
    captured = capsys.readouterr()
    assert "Workspace status:" in captured.out
    assert "1 documents" in captured.out


def test_cli_workspace_status_json(tmp_path: Path, capsys) -> None:
    init_workspace(tmp_path)

    assert main(["workspace", "status", "--root", str(tmp_path), "--json"]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["document_count"] == 0
    assert payload["candidate_count"] == 0
    assert payload["evidence_pack_count"] == 0
    assert payload["review_decision_count"] == 0
    assert payload["snapshot_count"] == 0


def test_workspace_review_decisions_and_items(tmp_path: Path) -> None:
    _write(
        tmp_path / "docs" / "billing.md",
        "Use `billing.update_credit_limit` for credit limit changes.\nCredit limit review happens first.\n",
    )
    ingest_report = ingest_local_paths([tmp_path / "docs"], root=tmp_path)
    candidate_report = discover_scout_candidates(ingest_report.documents, min_score=0.2, max_candidates=5)
    evidence_report = build_evidence_packs(ingest_report.documents, candidate_report.candidates, context_lines=0)

    state = init_workspace(tmp_path)
    state.store_ingest_report(ingest_report)
    state.store_candidate_report(candidate_report)
    state.store_evidence_report(evidence_report)

    decision = state.save_review_decision("billing.update_credit_limit", "accepted", note="Looks canonical")
    assert decision.decision.value == "accepted"

    items = state.list_review_items(limit=10)
    reviewed = [item for item in items if item.normalized_surface == "billing.update_credit_limit"]
    assert reviewed
    assert reviewed[0].review_status == "accepted"
    assert reviewed[0].review_decision is not None
    assert reviewed[0].review_decision.note == "Looks canonical"

    summary = state.summary()
    assert summary.review_decision_count == 1
