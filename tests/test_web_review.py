from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from agent_lexicon import (
    build_evidence_packs,
    build_review_inbox_html,
    discover_scout_candidates,
    ingest_local_paths,
    init_workspace,
)




def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    src_path = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONPATH"] = src_path if not existing_pythonpath else f"{src_path}{os.pathsep}{existing_pythonpath}"
    return env

def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _workspace_with_evidence(root: Path):
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


def test_review_inbox_html_renders_candidates_and_evidence(tmp_path: Path) -> None:
    state = _workspace_with_evidence(tmp_path)

    html = build_review_inbox_html(state, selected_surface="billing.update_credit_limit")

    assert "Proposal Inbox" in html
    assert "billing.update_credit_limit" in html
    assert "Positive evidence" in html
    assert "Negative evidence" in html
    assert "Review decision" in html
    assert "Accept" in html
    assert "Needs split" in html


def test_review_inbox_html_renders_saved_decision(tmp_path: Path) -> None:
    state = _workspace_with_evidence(tmp_path)
    state.save_review_decision("billing.update_credit_limit", "ambiguous", note="Needs owner review")

    html = build_review_inbox_html(state, selected_surface="billing.update_credit_limit")

    assert "Ambiguous" in html
    assert "Needs owner review" in html


def test_review_cli_help_does_not_start_server() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "agent_lexicon", "review", "--help"],
        check=True,
        text=True,
        capture_output=True,
        env=_subprocess_env(),
    )
    assert "Open the local web proposal inbox" in completed.stdout
    assert "--no-browser" in completed.stdout
