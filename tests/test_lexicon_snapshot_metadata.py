from __future__ import annotations

import json
from pathlib import Path

from agent_lexicon import (
    Lexicon,
    Term,
    build_evidence_packs,
    discover_scout_candidates,
    guard_tool_call,
    ingest_local_paths,
    init_workspace,
    lexicon_runtime_metadata,
    lexicon_snapshot_ref,
    publish_local_snapshot,
    resolve_text,
)
from agent_lexicon.cli import main
from agent_lexicon.scout import GitDiffAddedLine, build_git_merge_terminology_report


def test_lexicon_snapshot_ref_is_stable_and_content_addressed() -> None:
    lexicon = Lexicon(terms=(Term(id="billing.credit_limit", canonical="credit limit"),))
    same = Lexicon(terms=(Term(id="billing.credit_limit", canonical="credit limit"),))
    changed = Lexicon(terms=(Term(id="billing.credit_limit", canonical="customer limit"),))

    ref = lexicon_snapshot_ref(lexicon)

    assert ref.startswith("sha256:")
    assert ref == lexicon_snapshot_ref(same)
    assert ref != lexicon_snapshot_ref(changed)
    assert lexicon_runtime_metadata(lexicon)["lexicon_snapshot_ref"] == ref


def test_resolver_and_guard_attach_lexicon_snapshot_metadata() -> None:
    lexicon = Lexicon(
        terms=(
            Term(
                id="billing.credit_limit",
                canonical="credit limit",
                tools=("billing.update_credit_limit",),
            ),
        )
    )
    snapshot_ref = lexicon_snapshot_ref(lexicon)

    resolution = resolve_text(lexicon, "increase the credit limit")
    guard = guard_tool_call(
        lexicon,
        "increase the credit limit",
        tool_name="billing.update_credit_limit",
    )

    assert resolution.metadata["lexicon_snapshot_ref"] == snapshot_ref
    assert resolution.metadata["lexicon_snapshot"]["immutable"] is True
    assert guard.metadata["lexicon_snapshot_ref"] == snapshot_ref
    assert guard.resolution.metadata["lexicon_snapshot_ref"] == snapshot_ref


def test_git_merge_report_includes_lexicon_snapshot_metadata() -> None:
    lexicon = Lexicon(terms=(Term(id="auth.access_token", canonical="access token"),))
    report = build_git_merge_terminology_report(
        lexicon,
        (GitDiffAddedLine(path="src/auth.py", line_number=10, text="authToken = rotate()"),),
        lexicon_path="lexicon/lexicon.yaml",
    )
    payload = report.to_dict()

    assert payload["metadata"]["lexicon_snapshot_ref"] == lexicon_snapshot_ref(lexicon)
    assert payload["metadata"]["lexicon_snapshot"]["source_path"] == "lexicon/lexicon.yaml"
    assert "Lexicon snapshot: sha256:" in report.to_text()


def test_cli_resolve_prints_lexicon_snapshot(tmp_path: Path, capsys) -> None:
    path = tmp_path / "lexicon.yaml"
    path.write_text(
        """
        version: 1
        terms:
          - id: billing.credit_limit
            canonical: credit limit
        """,
        encoding="utf-8",
    )

    exit_code = main(["resolve", str(path), "increase credit limit"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Lexicon snapshot: sha256:" in captured.out


def test_published_snapshot_records_immutable_metadata(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "billing.md").write_text(
        "Use billing.update_credit_limit for credit limit changes.\n",
        encoding="utf-8",
    )
    ingest_report = ingest_local_paths([docs], root=tmp_path)
    candidate_report = discover_scout_candidates(ingest_report.documents, min_score=0.2, max_candidates=5)
    evidence_report = build_evidence_packs(ingest_report.documents, candidate_report.candidates, context_lines=0)
    state = init_workspace(tmp_path)
    state.store_ingest_report(ingest_report)
    state.store_candidate_report(candidate_report)
    state.store_evidence_report(evidence_report)
    state.save_review_decision("billing.update_credit_limit", "accepted")

    snapshot = publish_local_snapshot(state, output_path=tmp_path / "snapshot.json", snapshot_id="snapshot_audit")
    payload = json.loads(json.dumps(snapshot.to_dict()))

    assert payload["metadata"]["lexicon_snapshot_ref"].startswith("sha256:")
    assert payload["metadata"]["lexicon_snapshot"]["source_path"].endswith("snapshot.json")
