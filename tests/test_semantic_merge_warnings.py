from __future__ import annotations

import json
from pathlib import Path

from agent_lexicon import (
    SemanticMergeStatus,
    SemanticMergeWarningKind,
    lexicon_from_dict,
    merge_lexicons,
)
from agent_lexicon.cli import main


def _payload() -> dict:
    return {
        "version": 1,
        "scopes": [{"id": "auth", "label": "Auth"}],
        "terms": [
            {
                "id": "auth.access_token",
                "canonical": "access token",
                "description": "Token used to authorize API calls.",
                "scopes": ["auth"],
                "tags": ["auth"],
                "tools": ["auth.rotate_access_token"],
                "aliases": [{"surface": "access token", "scopes": ["auth"]}],
            }
        ],
        "proposals": [],
    }


def _write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def test_semantic_merge_warns_when_canonical_rename_merges_with_parallel_term_change() -> None:
    base_payload = _payload()
    ours_payload = _payload()
    theirs_payload = _payload()
    ours_payload["terms"][0]["aliases"].append({"surface": "bearer token", "scopes": ["auth"]})
    theirs_payload["terms"][0]["canonical"] = "auth token"

    report = merge_lexicons(
        lexicon_from_dict(base_payload),
        lexicon_from_dict(ours_payload),
        lexicon_from_dict(theirs_payload),
    )

    assert report.status == SemanticMergeStatus.CLEAN
    assert report.has_conflicts is False
    assert report.has_warnings is True
    assert report.warning_count == 1
    warning = report.warnings[0]
    assert warning.kind == SemanticMergeWarningKind.CANONICAL_RENAME_WITH_PARALLEL_TERM_CHANGE
    assert warning.object_id == "auth.access_token"
    assert warning.path == "terms[auth.access_token].canonical"
    assert "same concept" in warning.reason
    assert warning.to_dict()["kind"] == "canonical_rename_with_parallel_term_change"

    assert report.merged_lexicon is not None
    merged_term = report.merged_lexicon.get_term("auth.access_token")
    assert merged_term is not None
    assert merged_term.canonical == "auth token"
    assert "bearer token" in merged_term.surfaces()


def test_semantic_merge_does_not_warn_for_one_sided_canonical_change() -> None:
    base_payload = _payload()
    ours_payload = _payload()
    theirs_payload = _payload()
    theirs_payload["terms"][0]["canonical"] = "auth token"

    report = merge_lexicons(
        lexicon_from_dict(base_payload),
        lexicon_from_dict(ours_payload),
        lexicon_from_dict(theirs_payload),
    )

    assert report.status == SemanticMergeStatus.CLEAN
    assert report.warning_count == 0


def test_cli_dictionary_merge_text_and_json_include_semantic_warnings(tmp_path: Path, capsys) -> None:
    base_payload = _payload()
    ours_payload = _payload()
    theirs_payload = _payload()
    ours_payload["terms"][0]["aliases"].append({"surface": "bearer token", "scopes": ["auth"]})
    theirs_payload["terms"][0]["canonical"] = "auth token"

    base_path = _write_json(tmp_path / "base.json", base_payload)
    ours_path = _write_json(tmp_path / "ours.json", ours_payload)
    theirs_path = _write_json(tmp_path / "theirs.json", theirs_payload)

    assert main(["dictionary", "merge", str(base_path), str(ours_path), str(theirs_path), "--check"]) == 0
    text_output = capsys.readouterr().out
    assert "Semantic merge: clean" in text_output
    assert "Semantic warnings: 1" in text_output
    assert "canonical name changed" in text_output

    assert main(["dictionary", "merge", str(base_path), str(ours_path), str(theirs_path), "--json", "--check"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "clean"
    assert payload["warning_count"] == 1
    assert payload["warnings"][0]["kind"] == "canonical_rename_with_parallel_term_change"
