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


def test_clear_review_decision_returns_candidate_to_unreviewed(tmp_path: Path) -> None:
    from agent_lexicon import ReviewDecisionStatus

    state = _workspace_with_candidate(tmp_path)
    state.save_review_decision("billing.update_credit_limit", "accepted", note="Looks canonical")

    assert state.clear_review_decision("billing.update_credit_limit", note="Reset for another pass") is True

    item = state.get_review_item("billing.update_credit_limit")
    assert item is not None
    assert item.review_status == "unreviewed"
    assert item.review_decision is None

    summary = state.summary()
    assert summary.review_decision_count == 0
    assert summary.review_event_count == 2

    events = state.list_review_events()
    assert events[0].event_type == ReviewEventType.DECISION_SAVED
    assert events[1].event_type == ReviewEventType.DECISION_CLEARED
    assert events[1].decision == ReviewDecisionStatus.UNREVIEWED
    assert events[1].metadata["previous_decision"] == "accepted"
    assert events[1].note == "Reset for another pass"


def test_clear_review_decision_is_idempotent_for_unreviewed_candidate(tmp_path: Path) -> None:
    state = _workspace_with_candidate(tmp_path)

    assert state.clear_review_decision("billing.update_credit_limit") is False

    summary = state.summary()
    assert summary.review_decision_count == 0
    assert summary.review_event_count == 0


def test_review_decision_records_actor_and_git_metadata(tmp_path: Path) -> None:
    state = _workspace_with_candidate(tmp_path)

    saved = state.save_review_decision(
        "billing.update_credit_limit",
        "accepted",
        reviewer="local",
        actor_type="human",
        actor_source="web",
        actor_id="Maxim Nikolaev",
        git_metadata={
            "available": True,
            "author_name": "Maxim Nikolaev",
            "author_email": "maxim@example.com",
            "branch": "review-actor-provenance",
            "commit": "abc1234",
            "dirty": True,
        },
    )

    assert saved.metadata["actor"] == {
        "type": "human",
        "id": "Maxim Nikolaev",
        "source": "web",
    }
    assert saved.metadata["git"]["branch"] == "review-actor-provenance"
    assert saved.metadata["git"]["commit"] == "abc1234"
    assert saved.metadata["git"]["dirty"] is True

    event = state.list_review_events()[0]
    assert event.metadata["actor"]["id"] == "Maxim Nikolaev"
    assert event.metadata["actor"]["source"] == "web"
    assert event.metadata["git"]["author_email"] == "maxim@example.com"

    record = state.list_decision_records()[0]
    assert record.actor == "Maxim Nikolaev"
    assert record.rule_id == "human_review"
    assert record.metadata["actor"]["type"] == "human"
    assert record.metadata["git"]["commit"] == "abc1234"


def test_clear_review_decision_preserves_actor_metadata(tmp_path: Path) -> None:
    state = _workspace_with_candidate(tmp_path)
    state.save_review_decision("billing.update_credit_limit", "accepted")

    assert state.clear_review_decision(
        "billing.update_credit_limit",
        reviewer="local",
        actor_type="human",
        actor_source="web",
        actor_id="Maxim Nikolaev",
        git_metadata={
            "available": True,
            "author_name": "Maxim Nikolaev",
            "author_email": "maxim@example.com",
            "branch": "main",
            "commit": "def5678",
            "dirty": False,
        },
    ) is True

    clear_event = state.list_review_events()[-1]
    assert clear_event.event_type == ReviewEventType.DECISION_CLEARED
    assert clear_event.metadata["actor"] == {
        "type": "human",
        "id": "Maxim Nikolaev",
        "source": "web",
    }
    assert clear_event.metadata["git"]["commit"] == "def5678"
    assert clear_event.metadata["previous_decision"] == "accepted"
