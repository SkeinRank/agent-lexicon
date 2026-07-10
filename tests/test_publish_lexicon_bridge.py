"""`publish --update-lexicon` writes accepted terms back to the git-tracked
lexicon file, closing the gap between the snapshot JSON under
`.agent-lexicon/` and the dictionary-as-code YAML the README promises.
Found during an end-to-end run against apache/airflow: accepting 10 terms
and publishing left `lexicon/lexicon.yaml` untouched at the starter example.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_lexicon.core import load_lexicon
from agent_lexicon.ingest import ingest_local_paths
from agent_lexicon.scout import build_evidence_packs, discover_scout_candidates
from agent_lexicon.workflows.simple import SimpleWorkflowError, run_simple_init, run_simple_publish, run_simple_scan
from agent_lexicon.workspace import open_workspace


def _prepare_workspace(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "notes.md").write_text(
        "The ContextSpace snapshot owns every RuntimeSnapshot ContextSpace record.\n",
        encoding="utf-8",
    )
    run_simple_init(tmp_path)
    run_simple_scan(["docs"], root=tmp_path)
    state = open_workspace(tmp_path, create=False)
    state.save_review_decision("contextspace", "accepted")


def test_publish_without_flag_leaves_lexicon_untouched(tmp_path: Path) -> None:
    _prepare_workspace(tmp_path)
    lexicon_file = tmp_path / "lexicon" / "lexicon.yaml"
    before = lexicon_file.read_text(encoding="utf-8")

    report = run_simple_publish(tmp_path)

    assert lexicon_file.read_text(encoding="utf-8") == before
    assert report.metadata["lexicon_updated_path"] is None


def test_publish_update_lexicon_writes_terms_back(tmp_path: Path) -> None:
    _prepare_workspace(tmp_path)
    lexicon_file = tmp_path / "lexicon" / "lexicon.yaml"

    report = run_simple_publish(tmp_path, update_lexicon=True)

    assert report.metadata["lexicon_updated_path"] == str(lexicon_file)
    updated = load_lexicon(lexicon_file)
    canonicals = [term.canonical.casefold() for term in updated.terms]
    assert any("contextspace" in canonical for canonical in canonicals)
    # The starter placeholder does not come back after a real publish.
    assert "example term" not in canonicals


def test_updated_lexicon_roundtrips_and_matches_snapshot(tmp_path: Path) -> None:
    _prepare_workspace(tmp_path)
    lexicon_file = tmp_path / "lexicon" / "lexicon.yaml"

    report = run_simple_publish(tmp_path, update_lexicon=True)

    snapshot_payload = json.loads(Path(report.output_path).read_text(encoding="utf-8"))
    reloaded = load_lexicon(lexicon_file)
    assert [term.canonical for term in reloaded.terms] == [
        term["canonical"] for term in snapshot_payload["terms"]
    ]
    # Deterministic rewrite: publishing again with no new decisions is an
    # error before the file could change, so run the writer twice directly.
    from agent_lexicon.workflows.simple import _write_lexicon_file

    first = lexicon_file.read_text(encoding="utf-8")
    _write_lexicon_file(reloaded, lexicon_file)
    assert lexicon_file.read_text(encoding="utf-8") == first


def test_update_lexicon_supports_json_lexicon(tmp_path: Path) -> None:
    _prepare_workspace(tmp_path)
    json_lexicon = tmp_path / "lexicon.json"
    json_lexicon.write_text(json.dumps({"version": "1", "terms": []}), encoding="utf-8")

    report = run_simple_publish(tmp_path, lexicon_path=json_lexicon, update_lexicon=True)

    assert report.metadata["lexicon_updated_path"] == str(json_lexicon)
    reloaded = load_lexicon(json_lexicon)
    assert any("contextspace" in term.canonical.casefold() for term in reloaded.terms)
