from __future__ import annotations

import json
from pathlib import Path

from agent_lexicon import (
    WorkspaceDecisionAction,
    append_decision_record,
    build_evidence_packs,
    discover_scout_candidates,
    export_decision_records_jsonl,
    ingest_local_paths,
    init_workspace,
    list_decision_records,
    publish_local_snapshot,
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


def test_review_decision_adds_provenance_record(tmp_path: Path) -> None:
    state = _workspace_with_candidate(tmp_path)

    state.save_review_decision("billing.update_credit_limit", "accepted", note="Ready", reviewer="maxim")

    records = state.list_decision_records()
    assert len(records) == 1
    record = records[0]
    assert record.action == WorkspaceDecisionAction.REVIEW_DECISION_SAVED
    assert record.actor == "maxim"
    assert record.subject == "billing.update_credit_limit"
    assert record.result == "accepted"
    assert record.rule_id == "human_review"
    assert record.payload["review_decision"] == "accepted"
    assert record.payload["candidate_snapshot"]["surface"] == "billing.update_credit_limit"
    assert state.summary().decision_record_count == 1


def test_decision_records_can_be_appended_filtered_and_exported(tmp_path: Path) -> None:
    state = init_workspace(tmp_path)

    append_decision_record(
        state,
        actor="agent-a",
        action="runtime_resolve",
        subject="authToken",
        input_text="rotate authToken",
        result="unknown",
        rule_id="resolve_text",
        lexicon_snapshot_ref="sha256:abc",
        lexicon_fingerprint="abc",
        payload={"status": "unknown"},
    )
    append_decision_record(
        state,
        actor="agent-b",
        action="tool_guard",
        subject="billing.update_credit_limit",
        input_text="raise the credit limit",
        result="allow",
        rule_id="tool_guard",
    )

    runtime_records = list_decision_records(state, action="runtime_resolve")
    assert len(runtime_records) == 1
    assert runtime_records[0].actor == "agent-a"
    assert runtime_records[0].lexicon_snapshot_ref == "sha256:abc"

    content = export_decision_records_jsonl(state, action="runtime_resolve")
    rows = [json.loads(line) for line in content.splitlines()]
    assert len(rows) == 1
    assert rows[0]["action"] == "runtime_resolve"
    assert rows[0]["payload"]["status"] == "unknown"


def test_publish_snapshot_adds_snapshot_provenance_record(tmp_path: Path) -> None:
    state = _workspace_with_candidate(tmp_path)
    state.save_review_decision("billing.update_credit_limit", "accepted", note="Ready")

    snapshot = publish_local_snapshot(state, output_path=tmp_path / "snapshot.json", snapshot_id="snap_a")

    records = state.list_decision_records(action=WorkspaceDecisionAction.SNAPSHOT_PUBLISHED)
    assert len(records) == 1
    assert records[0].subject == "snap_a"
    assert records[0].result == "published"
    assert records[0].lexicon_snapshot_ref == snapshot.metadata["lexicon_snapshot_ref"]
    assert records[0].lexicon_fingerprint == snapshot.metadata["lexicon_fingerprint"]


def test_cli_workspace_export_decision_log_stdout_and_file(tmp_path: Path, capsys) -> None:
    state = _workspace_with_candidate(tmp_path)
    state.save_review_decision("billing.update_credit_limit", "accepted", note="Ready")

    assert main(["workspace", "export-decision-log", "--root", str(tmp_path)]) == 0
    captured = capsys.readouterr()
    stdout_rows = [json.loads(line) for line in captured.out.splitlines()]
    assert stdout_rows[0]["action"] == "review_decision_saved"
    assert stdout_rows[0]["rule_id"] == "human_review"

    output_path = tmp_path / "decision-log.jsonl"
    assert main([
        "workspace",
        "export-decision-log",
        "--root",
        str(tmp_path),
        "--output",
        str(output_path),
        "--action",
        "review_decision_saved",
    ]) == 0
    captured = capsys.readouterr()
    assert "Decision log exported: 1 records" in captured.out
    assert output_path.exists()
