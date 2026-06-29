from __future__ import annotations

import json
from pathlib import Path

from agent_lexicon import (
    ReviewEventType,
    build_evidence_packs,
    discover_scout_candidates,
    export_review_events_jsonl,
    ingest_local_paths,
    init_workspace,
    list_review_events,
)
from agent_lexicon.cli import main


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _workspace_with_candidate(root: Path):
    _write(
        root / "docs" / "billing.md",
        "Use `billing.update_credit_limit` for credit limit changes.\nCredit limit review happens first.\n",
    )
    ingest_report = ingest_local_paths([root / "docs"], root=root)
    candidate_report = discover_scout_candidates(ingest_report.documents, min_score=0.2, max_candidates=5)
    evidence_report = build_evidence_packs(ingest_report.documents, candidate_report.candidates, context_lines=0)
    state = init_workspace(root)
    state.store_ingest_report(ingest_report)
    state.store_candidate_report(candidate_report)
    state.store_evidence_report(evidence_report)
    return state


def test_save_review_decision_appends_review_events(tmp_path: Path) -> None:
    state = _workspace_with_candidate(tmp_path)

    state.save_review_decision("billing.update_credit_limit", "accepted", note="Looks canonical")
    state.save_review_decision("billing.update_credit_limit", "needs_split", note="Split API and billing wording")

    summary = state.summary()
    assert summary.review_decision_count == 1
    assert summary.review_event_count == 2

    events = state.list_review_events()
    assert len(events) == 2
    assert events[0].event_type == ReviewEventType.DECISION_SAVED
    assert events[0].decision.value == "accepted"
    assert events[0].note == "Looks canonical"
    assert events[0].candidate_snapshot["surface"] == "billing.update_credit_limit"
    assert events[0].evidence_snapshot["surface"] == "billing.update_credit_limit"
    assert events[1].decision.value == "needs_split"


def test_review_events_can_be_filtered_and_exported_as_jsonl(tmp_path: Path) -> None:
    state = _workspace_with_candidate(tmp_path)
    state.save_review_decision("billing.update_credit_limit", "accepted", note="Ready")
    state.save_review_decision("credit limit", "rejected", note="Already covered")

    accepted_events = list_review_events(state, decision="accepted")
    assert len(accepted_events) == 1
    assert accepted_events[0].normalized_surface == "billing.update_credit_limit"

    content = export_review_events_jsonl(state, decision="accepted")
    rows = [json.loads(line) for line in content.splitlines()]
    assert len(rows) == 1
    assert rows[0]["event_type"] == "review_decision_saved"
    assert rows[0]["decision"] == "accepted"
    assert rows[0]["candidate_snapshot"]["surface"] == "billing.update_credit_limit"


def test_review_events_export_writes_file(tmp_path: Path) -> None:
    state = _workspace_with_candidate(tmp_path)
    state.save_review_decision("billing.update_credit_limit", "accepted")

    output_path = tmp_path / "exports" / "review-events.jsonl"
    content = state.export_review_events_jsonl(output_path)

    assert output_path.read_text(encoding="utf-8") == content
    assert json.loads(content.splitlines()[0])["normalized_surface"] == "billing.update_credit_limit"


def test_cli_workspace_export_review_events_stdout_and_file(tmp_path: Path, capsys) -> None:
    state = _workspace_with_candidate(tmp_path)
    state.save_review_decision("billing.update_credit_limit", "accepted", note="Ready")

    assert main(["workspace", "export-review-events", "--root", str(tmp_path)]) == 0
    captured = capsys.readouterr()
    stdout_rows = [json.loads(line) for line in captured.out.splitlines()]
    assert stdout_rows[0]["decision"] == "accepted"
    assert stdout_rows[0]["note"] == "Ready"

    output_path = tmp_path / "review-events.jsonl"
    assert main([
        "workspace",
        "export-review-events",
        "--root",
        str(tmp_path),
        "--output",
        str(output_path),
    ]) == 0
    captured = capsys.readouterr()
    assert "Review events exported: 1 events" in captured.out
    assert output_path.exists()
