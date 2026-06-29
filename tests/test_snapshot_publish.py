from __future__ import annotations

import json
from pathlib import Path

from agent_lexicon import (
    Lexicon,
    SnapshotPublishError,
    Term,
    build_evidence_packs,
    discover_scout_candidates,
    ingest_local_paths,
    init_workspace,
    list_snapshots,
    load_lexicon,
    publish_local_snapshot,
)
from agent_lexicon.cli import main


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _workspace_with_reviewed_candidate(root: Path):
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
    state.save_review_decision("billing.update_credit_limit", "accepted", note="Ready for snapshot")
    return state


def test_publish_local_snapshot_writes_valid_lexicon(tmp_path: Path) -> None:
    state = _workspace_with_reviewed_candidate(tmp_path)
    output_path = tmp_path / "snapshots" / "local.json"

    snapshot = publish_local_snapshot(
        state,
        output_path=output_path,
        snapshot_id="snapshot_test",
    )

    assert snapshot.snapshot_id == "snapshot_test"
    assert snapshot.output_path == str(output_path)
    assert snapshot.accepted_count == 1
    assert snapshot.generated_term_count == 1
    assert output_path.exists()

    lexicon = load_lexicon(output_path)
    assert lexicon.get_term("billing.update_credit_limit") is not None
    term = lexicon.get_term("billing.update_credit_limit")
    assert term is not None
    assert term.canonical == "billing.update_credit_limit"
    assert term.evidence
    assert term.metadata["review_decision"]["note"] == "Ready for snapshot"

    records = list_snapshots(state)
    assert len(records) == 1
    assert records[0].snapshot_id == "snapshot_test"
    assert state.summary().snapshot_count == 1


def test_publish_local_snapshot_can_include_base_lexicon_and_skip_existing_surfaces(tmp_path: Path) -> None:
    state = _workspace_with_reviewed_candidate(tmp_path)
    base_lexicon = Lexicon(terms=(Term(id="billing.update_credit_limit", canonical="billing.update_credit_limit"),))

    snapshot = publish_local_snapshot(
        state,
        output_path=tmp_path / "snapshot.json",
        base_lexicon=base_lexicon,
        snapshot_id="snapshot_base",
    )

    assert snapshot.term_count == 1
    assert snapshot.accepted_count == 1
    assert snapshot.generated_term_count == 0
    assert snapshot.skipped_count == 1
    assert snapshot.skipped_surfaces == ("billing.update_credit_limit",)


def test_publish_local_snapshot_requires_accepted_reviews(tmp_path: Path) -> None:
    state = init_workspace(tmp_path)

    try:
        publish_local_snapshot(state, output_path=tmp_path / "snapshot.json")
    except SnapshotPublishError as exc:
        assert "no accepted review decisions" in str(exc)
    else:  # pragma: no cover - defensive branch
        raise AssertionError("expected SnapshotPublishError")


def test_cli_workspace_publish_snapshot(tmp_path: Path, capsys) -> None:
    state = _workspace_with_reviewed_candidate(tmp_path)
    output_path = tmp_path / "published" / "snapshot.json"

    assert main([
        "workspace",
        "publish-snapshot",
        "--root",
        str(tmp_path),
        "--output",
        str(output_path),
        "--snapshot-id",
        "snapshot_cli",
    ]) == 0
    captured = capsys.readouterr()
    assert "Snapshot published:" in captured.out
    assert "snapshot_cli" in captured.out
    assert output_path.exists()
    assert load_lexicon(output_path).get_term("billing.update_credit_limit") is not None
    assert state.summary().snapshot_count == 1


def test_cli_workspace_publish_snapshot_json(tmp_path: Path, capsys) -> None:
    _workspace_with_reviewed_candidate(tmp_path)
    output_path = tmp_path / "snapshot.json"

    assert main([
        "workspace",
        "publish-snapshot",
        "--root",
        str(tmp_path),
        "--output",
        str(output_path),
        "--snapshot-id",
        "snapshot_json",
        "--json",
    ]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["snapshot_id"] == "snapshot_json"
    assert payload["generated_term_count"] == 1
    assert payload["output_path"] == str(output_path)
