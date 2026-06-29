from __future__ import annotations

import json
from pathlib import Path

from agent_lexicon import (
    SimpleWorkflowError,
    init_workspace,
    run_simple_analyze,
    run_simple_init,
    run_simple_publish,
    run_simple_scan,
)
from agent_lexicon.cli import main
from agent_lexicon.workspace import open_workspace


def _write_project(root: Path) -> None:
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text(
        "PaymentCore owns the customer cap workflow.\n"
        "Use billing.update_credit_limit when CustomerCapReview approves a customer cap.\n",
        encoding="utf-8",
    )
    (root / "docs" / "billing.md").write_text(
        "CustomerCapReview validates customer cap changes before rollout.\n"
        "PaymentCore stores customer cap audit events.\n",
        encoding="utf-8",
    )
    (root / "src" / "billing.py").write_text(
        "class CustomerCapReview:\n"
        "    tool_name = 'billing.update_credit_limit'\n",
        encoding="utf-8",
    )


def test_simple_init_creates_dictionary_workspace_and_policy(tmp_path: Path) -> None:
    report = run_simple_init(tmp_path)

    assert report.dictionary.valid
    assert Path(report.workspace.db_path).exists()
    assert Path(report.policy_path).exists()
    assert report.policy_mode == "solo"


def test_simple_scan_and_analyze_store_prioritized_candidates(tmp_path: Path) -> None:
    _write_project(tmp_path)
    run_simple_init(tmp_path)

    scan = run_simple_scan(["README.md", "docs", "src"], root=tmp_path, max_candidates=5, min_score=0.2)

    assert scan.document_count == 3
    assert scan.candidate_count > 0
    assert scan.evidence_pack_count > 0
    assert scan.safety.highest_risk.value == "none"

    analyze = run_simple_analyze(tmp_path, limit=5, include_review_agent=True)
    assert analyze.item_count > 0
    assert analyze.items[0].priority in {"important", "later"}
    assert analyze.items[0].review_status == "unreviewed"
    assert analyze.items[0].recommendation is not None

    consensus_analyze = run_simple_analyze(tmp_path, limit=5, include_review_agent=True, include_review_agent_consensus=True)
    assert consensus_analyze.items[0].consensus_status in {"consensus", "abstain", "blocked"}
    assert consensus_analyze.items[0].agreement_ratio is not None


def test_simple_publish_uses_accepted_review_decisions(tmp_path: Path) -> None:
    _write_project(tmp_path)
    run_simple_init(tmp_path)
    run_simple_scan(["README.md", "docs", "src"], root=tmp_path, max_candidates=5, min_score=0.2)
    state = open_workspace(tmp_path, create=False)
    first_item = state.list_review_items(limit=1)[0]
    state.save_review_decision(first_item.normalized_surface, "accepted", note="Looks canonical")

    report = run_simple_publish(tmp_path)

    assert Path(report.output_path).exists()
    assert report.accepted_count == 1
    assert report.generated_term_count >= 1
    assert report.term_count >= 1


def test_simple_scan_rejects_missing_default_paths(tmp_path: Path) -> None:
    run_simple_init(tmp_path)

    try:
        run_simple_scan(root=tmp_path)
    except SimpleWorkflowError as exc:
        assert "no scan paths exist" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("expected SimpleWorkflowError")


def test_cli_simple_init_scan_analyze_publish(tmp_path: Path, capsys) -> None:
    _write_project(tmp_path)

    assert main(["init", "--root", str(tmp_path)]) == 0
    assert "Agent Lexicon initialized" in capsys.readouterr().out

    assert main([
        "scan",
        "README.md",
        "docs",
        "src",
        "--root",
        str(tmp_path),
        "--max-candidates",
        "5",
        "--min-score",
        "0.2",
    ]) == 0
    scan_output = capsys.readouterr().out
    assert "Agent Lexicon scan:" in scan_output
    assert "evidence packs saved" in scan_output

    assert main(["analyze", "--root", str(tmp_path), "--review-agent", "--consensus"]) == 0
    analyze_output = capsys.readouterr().out
    assert "Agent Lexicon analyze:" in analyze_output
    assert "Next: agent-lexicon review" in analyze_output
    assert "consensus=" in analyze_output

    state = open_workspace(tmp_path, create=False)
    first_item = state.list_review_items(limit=1)[0]
    state.save_review_decision(first_item.normalized_surface, "accepted", note="Looks canonical")

    assert main(["publish", "--root", str(tmp_path)]) == 0
    publish_output = capsys.readouterr().out
    assert "Snapshot published:" in publish_output


def test_cli_simple_json_outputs(tmp_path: Path, capsys) -> None:
    _write_project(tmp_path)

    assert main(["init", "--root", str(tmp_path), "--json"]) == 0
    init_payload = json.loads(capsys.readouterr().out)
    assert init_payload["dictionary"]["valid"] is True

    assert main(["scan", "docs", "--root", str(tmp_path), "--json", "--max-candidates", "3"]) == 0
    scan_payload = json.loads(capsys.readouterr().out)
    assert scan_payload["document_count"] == 1
    assert scan_payload["candidate_count"] >= 1

    assert main(["analyze", "--root", str(tmp_path), "--json"]) == 0
    analyze_payload = json.loads(capsys.readouterr().out)
    assert analyze_payload["item_count"] >= 1
