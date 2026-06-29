from __future__ import annotations

import json
from pathlib import Path

from agent_lexicon import (
    SemanticChangeKind,
    SemanticObjectKind,
    diff_lexicon_files,
    diff_lexicons,
    lexicon_from_dict,
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


def test_semantic_diff_reports_no_changes_for_equivalent_lexicons() -> None:
    before = lexicon_from_dict(_base_payload())
    after = lexicon_from_dict(_base_payload())

    report = diff_lexicons(before, after)

    assert report.has_changes is False
    assert report.summary.total == 0
    assert report.to_dict()["changes"] == []


def test_semantic_diff_detects_term_scope_alias_tool_and_metadata_changes() -> None:
    before_payload = _base_payload()
    after_payload = _base_payload()
    after_payload["metadata"] = {"name": "Customer limits", "owner": "platform"}
    after_payload["scopes"][0]["label"] = "Billing Domain"
    after_payload["terms"][0]["canonical"] = "customer credit limit"
    after_payload["terms"][0]["tools"] = ["billing.set_credit_limit"]
    after_payload["terms"][0]["aliases"][0]["deprecated"] = True
    after_payload["terms"][0]["aliases"].append({"surface": "account cap", "scopes": ["billing"]})

    report = diff_lexicons(lexicon_from_dict(before_payload), lexicon_from_dict(after_payload))
    changes = report.to_dict()["changes"]

    assert report.has_changes is True
    assert any(change["object_kind"] == "lexicon" and change["field"] == "metadata" for change in changes)
    assert any(change["object_kind"] == "scope" and change["field"] == "label" for change in changes)
    assert any(change["object_kind"] == "term" and change["field"] == "canonical" for change in changes)
    assert any(change["object_kind"] == "tool" and change["change"] == "removed" for change in changes)
    assert any(change["object_kind"] == "tool" and change["change"] == "added" for change in changes)
    assert any(change["object_kind"] == "alias" and change["field"] == "deprecated" for change in changes)
    assert any(change["object_kind"] == "alias" and change["change"] == "added" for change in changes)


def test_semantic_diff_detects_added_and_removed_terms() -> None:
    before_payload = _base_payload()
    after_payload = _base_payload()
    after_payload["terms"] = [
        {
            "id": "billing.rate_limit",
            "canonical": "rate limit",
            "scopes": ["billing"],
        }
    ]

    report = diff_lexicons(lexicon_from_dict(before_payload), lexicon_from_dict(after_payload))
    changes = {(change.change, change.object_kind, change.object_id) for change in report.changes}

    assert (SemanticChangeKind.REMOVED, SemanticObjectKind.TERM, "billing.credit_limit") in changes
    assert (SemanticChangeKind.ADDED, SemanticObjectKind.TERM, "billing.rate_limit") in changes


def test_semantic_diff_files_returns_json_serializable_report(tmp_path: Path) -> None:
    before_path = _write_json(tmp_path / "before.json", _base_payload())
    after_payload = _base_payload()
    after_payload["terms"][0]["description"] = "Updated definition."
    after_path = _write_json(tmp_path / "after.json", after_payload)

    report = diff_lexicon_files(before_path, after_path)
    payload = json.loads(report.to_json())

    assert payload["has_changes"] is True
    assert payload["summary"]["changed"] == 1
    assert payload["changes"][0]["field"] == "description"


def test_cli_dictionary_diff_text_and_fail_on_change(tmp_path: Path, capsys) -> None:
    before_path = _write_json(tmp_path / "before.json", _base_payload())
    after_payload = _base_payload()
    after_payload["terms"][0]["canonical"] = "customer credit limit"
    after_path = _write_json(tmp_path / "after.json", after_payload)

    assert main(["dictionary", "diff", str(before_path), str(after_path)]) == 0
    output = capsys.readouterr().out
    assert "Semantic diff: 1 changes" in output
    assert "~ term billing.credit_limit.canonical" in output

    assert main(["dictionary", "diff", str(before_path), str(after_path), "--fail-on-change"]) == 1


def test_cli_dictionary_diff_json(tmp_path: Path, capsys) -> None:
    before_path = _write_json(tmp_path / "before.json", _base_payload())
    after_path = _write_json(tmp_path / "after.json", _base_payload())

    assert main(["dictionary", "diff", str(before_path), str(after_path), "--json", "--fail-on-change"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["has_changes"] is False
    assert payload["summary"]["total"] == 0
