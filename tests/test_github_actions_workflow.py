from __future__ import annotations

from pathlib import Path

import pytest

from agent_lexicon.cli import main


WORKFLOW_PATH = Path(".github/workflows/agent-lexicon.yml")


def test_agent_lexicon_workflow_uses_real_cli_commands() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "Agent Lexicon Terminology Review" in workflow
    assert "poetry run agent-lexicon validate lexicon/lexicon.yaml --lint --strict-lint" in workflow
    assert "poetry run agent-lexicon check-merge" in workflow
    assert "--base \"origin/${{ github.base_ref }}\"" in workflow
    assert "--head HEAD" in workflow
    assert "--fail-on-review" in workflow


def test_agent_lexicon_workflow_is_review_first_by_default() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "AGENT_LEXICON_FAIL_ON_REVIEW" in workflow
    assert "github.event.inputs.fail_on_review || 'false'" in workflow
    assert "if [ \"${AGENT_LEXICON_FAIL_ON_REVIEW}\" = \"true\" ]; then" in workflow


def test_cli_commands_documented_by_workflow_exist(capsys) -> None:
    with pytest.raises(SystemExit) as validate_exit:
        main(["validate", "--help"])
    assert validate_exit.value.code == 0
    validate_help = capsys.readouterr().out
    assert "--lint" in validate_help
    assert "--strict-lint" in validate_help

    with pytest.raises(SystemExit) as merge_exit:
        main(["check-merge", "--help"])
    assert merge_exit.value.code == 0
    merge_help = capsys.readouterr().out
    assert "--base" in merge_help
    assert "--head" in merge_help
    assert "--fail-on-review" in merge_help
