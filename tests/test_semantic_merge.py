from __future__ import annotations

import json
from pathlib import Path

from agent_lexicon import (
    SemanticMergeStatus,
    lexicon_from_dict,
    merge_lexicon_files,
    merge_lexicons,
)
from agent_lexicon.cli import main


def _base_payload() -> dict:
    return {
        "version": 1,
        "metadata": {"name": "Customer limits"},
        "scopes": [
            {"id": "billing", "label": "Billing"},
        ],
        "terms": [
            {
                "id": "billing.credit_limit",
                "canonical": "credit limit",
                "description": "Maximum allowed customer balance.",
                "scopes": ["billing"],
                "tags": ["billing"],
                "tools": ["billing.update_credit_limit"],
                "aliases": [
                    {"surface": "customer cap", "scopes": ["billing"]},
                ],
                "evidence": [
                    {
                        "source_path": "docs/billing.md",
                        "start_line": 3,
                        "snippet": "Customer cap is the credit limit.",
                        "kind": "positive",
                    }
                ],
            }
        ],
        "proposals": [],
    }


def _write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def test_semantic_merge_combines_non_overlapping_term_changes() -> None:
    base_payload = _base_payload()
    ours_payload = _base_payload()
    theirs_payload = _base_payload()
    ours_payload["terms"][0]["aliases"].append({"surface": "account cap", "scopes": ["billing"]})
    theirs_payload["terms"][0]["tools"].append("billing.preview_credit_limit")
    theirs_payload["terms"][0]["metadata"] = {"owner": "billing-platform"}

    report = merge_lexicons(
        lexicon_from_dict(base_payload),
        lexicon_from_dict(ours_payload),
        lexicon_from_dict(theirs_payload),
    )

    assert report.status == SemanticMergeStatus.CLEAN
    assert report.has_conflicts is False
    assert report.merged_lexicon is not None
    merged_term = report.merged_lexicon.get_term("billing.credit_limit")
    assert merged_term is not None
    assert "billing.preview_credit_limit" in merged_term.tools
    assert "account cap" in merged_term.surfaces()
    assert merged_term.metadata["owner"] == "billing-platform"


def test_semantic_merge_reports_conflict_for_same_canonical_change() -> None:
    base_payload = _base_payload()
    ours_payload = _base_payload()
    theirs_payload = _base_payload()
    ours_payload["terms"][0]["canonical"] = "account credit limit"
    theirs_payload["terms"][0]["canonical"] = "customer credit limit"

    report = merge_lexicons(
        lexicon_from_dict(base_payload),
        lexicon_from_dict(ours_payload),
        lexicon_from_dict(theirs_payload),
    )

    assert report.status == SemanticMergeStatus.CONFLICT
    assert report.merged_lexicon is None
    assert report.conflict_count == 1
    assert "canonical" in report.conflicts[0].path


def test_semantic_merge_reports_conflict_for_remove_vs_change() -> None:
    base_payload = _base_payload()
    ours_payload = _base_payload()
    theirs_payload = _base_payload()
    ours_payload["terms"] = []
    theirs_payload["terms"][0]["description"] = "Updated definition."

    report = merge_lexicons(
        lexicon_from_dict(base_payload),
        lexicon_from_dict(ours_payload),
        lexicon_from_dict(theirs_payload),
    )

    assert report.status == SemanticMergeStatus.CONFLICT
    assert report.conflict_count == 1
    assert "removed object" in report.conflicts[0].reason


def test_semantic_merge_files_and_cli_write_output(tmp_path: Path, capsys) -> None:
    base_payload = _base_payload()
    ours_payload = _base_payload()
    theirs_payload = _base_payload()
    ours_payload["scopes"][0]["description"] = "Billing terminology."
    theirs_payload["terms"][0]["aliases"].append({"surface": "limit cap", "scopes": ["billing"]})

    base_path = _write_json(tmp_path / "base.json", base_payload)
    ours_path = _write_json(tmp_path / "ours.json", ours_payload)
    theirs_path = _write_json(tmp_path / "theirs.json", theirs_payload)
    output_path = tmp_path / "merged.json"

    report = merge_lexicon_files(base_path, ours_path, theirs_path)
    assert report.status == SemanticMergeStatus.CLEAN

    assert main(["dictionary", "merge", str(base_path), str(ours_path), str(theirs_path), "--output", str(output_path)]) == 0
    output = capsys.readouterr().out
    assert "Semantic merge: clean" in output
    assert "Merged lexicon written" in output
    merged_payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert merged_payload["scopes"][0]["description"] == "Billing terminology."
    assert any(alias["surface"] == "limit cap" for alias in merged_payload["terms"][0]["aliases"])


def test_cli_dictionary_merge_json_conflict(tmp_path: Path, capsys) -> None:
    base_payload = _base_payload()
    ours_payload = _base_payload()
    theirs_payload = _base_payload()
    ours_payload["metadata"]["owner"] = "billing"
    theirs_payload["metadata"]["owner"] = "platform"

    base_path = _write_json(tmp_path / "base.json", base_payload)
    ours_path = _write_json(tmp_path / "ours.json", ours_payload)
    theirs_path = _write_json(tmp_path / "theirs.json", theirs_payload)

    assert main(["dictionary", "merge", str(base_path), str(ours_path), str(theirs_path), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "conflict"
    assert payload["conflict_count"] == 1
    assert payload["conflicts"][0]["path"] == "metadata.owner"
