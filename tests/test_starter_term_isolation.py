"""The starter placeholder term from `init` must never become working
vocabulary: it is excluded from published snapshots and from agent-facing
context output. Found during an end-to-end run against apache/airflow, where
`context` told the agent to use the canonical term "example term".
"""

from __future__ import annotations

import json
from pathlib import Path

from agent_lexicon.core import load_lexicon
from agent_lexicon.dictionary.layout import STARTER_LEXICON_YAML


def _write_starter_lexicon(tmp_path: Path) -> Path:
    path = tmp_path / "lexicon.yaml"
    path.write_text(STARTER_LEXICON_YAML, encoding="utf-8")
    return path


def test_starter_template_term_is_flagged(tmp_path: Path) -> None:
    lexicon = load_lexicon(_write_starter_lexicon(tmp_path))
    starter = lexicon.get_term("project.example_term")
    assert starter is not None
    assert starter.is_starter is True


def test_publish_drops_starter_terms(tmp_path: Path) -> None:
    from agent_lexicon.ingest import ingest_local_paths
    from agent_lexicon.scout import build_evidence_packs, discover_scout_candidates
    from agent_lexicon.workspace import init_workspace
    from agent_lexicon.workspace.snapshot import publish_local_snapshot

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "notes.md").write_text(
        "The ContextSpace snapshot owns every RuntimeSnapshot ContextSpace record.\n",
        encoding="utf-8",
    )
    ingest_report = ingest_local_paths([docs], root=tmp_path)
    candidate_report = discover_scout_candidates(ingest_report.documents, min_score=0.2, max_candidates=5)
    evidence_report = build_evidence_packs(ingest_report.documents, candidate_report.candidates, context_lines=0)
    state = init_workspace(tmp_path)
    state.store_ingest_report(ingest_report)
    state.store_candidate_report(candidate_report)
    state.store_evidence_report(evidence_report)
    state.save_review_decision("contextspace", "accepted")

    lexicon = load_lexicon(_write_starter_lexicon(tmp_path))
    snapshot = publish_local_snapshot(
        state,
        output_path=tmp_path / "snap.json",
        base_lexicon=lexicon,
    )
    published = json.loads(Path(snapshot.output_path).read_text(encoding="utf-8"))
    canonicals = [term["canonical"] for term in published["terms"]]
    assert "example term" not in canonicals
    assert any("contextspace" in canonical.casefold() for canonical in canonicals)
    assert snapshot.metadata["starter_terms_dropped"] == ["project.example_term"]


def test_context_hides_starter_terms(tmp_path: Path, capsys) -> None:
    from agent_lexicon.cli import main

    path = _write_starter_lexicon(tmp_path)
    exit_code = main(["context", str(path)])
    output = capsys.readouterr().out
    assert exit_code == 0
    assert "example term" not in output
    assert "example concept" not in output
