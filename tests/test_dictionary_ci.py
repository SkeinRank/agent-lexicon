from __future__ import annotations

import json
from pathlib import Path

from agent_lexicon import (
    DictionaryCheckKind,
    DictionaryCheckStatus,
    init_dictionary_layout,
    load_lexicon,
    run_dictionary_pr_checks,
)
from agent_lexicon.cli import main


def _write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _starter_payload(root: Path) -> dict:
    summary = init_dictionary_layout(root)
    return load_lexicon(summary.layout.lexicon_path).to_dict()


def test_run_dictionary_pr_checks_valid_layout_and_behavior(tmp_path: Path) -> None:
    init_dictionary_layout(tmp_path)

    report = run_dictionary_pr_checks(tmp_path)

    assert report.passed is True
    assert report.failed_count == 0
    assert [item.kind for item in report.checks] == [
        DictionaryCheckKind.LAYOUT,
        DictionaryCheckKind.BEHAVIOR,
    ]
    assert report.checks[0].status is DictionaryCheckStatus.PASSED
    assert report.checks[1].status is DictionaryCheckStatus.PASSED


def test_run_dictionary_pr_checks_fails_on_invalid_layout(tmp_path: Path) -> None:
    report = run_dictionary_pr_checks(tmp_path)

    assert report.passed is False
    assert report.checks[0].kind is DictionaryCheckKind.LAYOUT
    assert report.checks[0].status is DictionaryCheckStatus.FAILED
    assert report.checks[1].kind is DictionaryCheckKind.BEHAVIOR
    assert report.checks[1].status is DictionaryCheckStatus.SKIPPED


def test_run_dictionary_pr_checks_reports_semantic_diff_without_failing(tmp_path: Path) -> None:
    payload = _starter_payload(tmp_path)
    base_path = _write_json(tmp_path / "base.json", payload)
    lexicon_path = tmp_path / "lexicon" / "lexicon.yaml"
    lexicon_path.write_text(
        lexicon_path.read_text(encoding="utf-8").replace("example term", "project term"),
        encoding="utf-8",
    )

    report = run_dictionary_pr_checks(tmp_path, base_lexicon_path=base_path)

    assert report.passed is True
    diff_item = [item for item in report.checks if item.kind is DictionaryCheckKind.SEMANTIC_DIFF][0]
    assert diff_item.status is DictionaryCheckStatus.PASSED
    assert diff_item.details["has_changes"] is True


def test_run_dictionary_pr_checks_can_fail_on_semantic_change(tmp_path: Path) -> None:
    payload = _starter_payload(tmp_path)
    base_path = _write_json(tmp_path / "base.json", payload)
    lexicon_path = tmp_path / "lexicon" / "lexicon.yaml"
    lexicon_path.write_text(
        lexicon_path.read_text(encoding="utf-8").replace("example term", "project term"),
        encoding="utf-8",
    )

    report = run_dictionary_pr_checks(
        tmp_path,
        base_lexicon_path=base_path,
        fail_on_semantic_change=True,
    )

    assert report.passed is False
    diff_item = [item for item in report.checks if item.kind is DictionaryCheckKind.SEMANTIC_DIFF][0]
    assert diff_item.status is DictionaryCheckStatus.FAILED


def test_run_dictionary_pr_checks_reports_semantic_merge_conflict(tmp_path: Path) -> None:
    payload = _starter_payload(tmp_path)
    base_path = _write_json(tmp_path / "base.json", payload)
    ours_payload = json.loads(json.dumps(payload))
    theirs_payload = json.loads(json.dumps(payload))
    ours_payload["terms"][0]["canonical"] = "ours term"
    theirs_payload["terms"][0]["canonical"] = "theirs term"
    ours_path = _write_json(tmp_path / "ours.json", ours_payload)
    theirs_path = _write_json(tmp_path / "theirs.json", theirs_payload)

    report = run_dictionary_pr_checks(
        tmp_path,
        merge_base_path=base_path,
        merge_ours_path=ours_path,
        merge_theirs_path=theirs_path,
    )

    assert report.passed is False
    merge_item = [item for item in report.checks if item.kind is DictionaryCheckKind.SEMANTIC_MERGE][0]
    assert merge_item.status is DictionaryCheckStatus.FAILED
    assert merge_item.details["has_conflicts"] is True


def test_cli_dictionary_pr_check_text_and_json(tmp_path: Path, capsys) -> None:
    init_dictionary_layout(tmp_path)

    assert main(["dictionary", "pr-check", "--root", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert "Dictionary PR check: passed" in output
    assert "[PASSED] layout" in output
    assert "[PASSED] behavior" in output

    assert main(["dictionary", "pr-check", "--root", str(tmp_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is True
    assert payload["summary"]["failed"] == 0


def test_cli_dictionary_pr_check_merge_requires_all_inputs(tmp_path: Path, capsys) -> None:
    init_dictionary_layout(tmp_path)

    assert main(["dictionary", "pr-check", "--root", str(tmp_path), "--merge-base", "base.json"]) == 1
    output = capsys.readouterr().out
    assert "semantic merge requires" in output
