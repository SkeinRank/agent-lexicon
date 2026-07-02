from __future__ import annotations

import json
from pathlib import Path

from agent_lexicon.cli import main


EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples" / "customer_limits"
LEXICON = str(EXAMPLES_DIR / "lexicon.yaml")


def test_resolve_json_emits_decision(capsys) -> None:
    exit_code = main(["resolve", LEXICON, "please raise the limit", "--json"])
    captured = capsys.readouterr()

    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["status"] == "ambiguous"
    assert payload["action"] == "ask_clarification"
    term_ids = {candidate["term_id"] for candidate in payload["candidates"]}
    assert term_ids == {"api.rate_limit", "billing.credit_limit"}


def test_guard_json_preserves_block_exit_code(capsys) -> None:
    exit_code = main([
        "guard",
        LEXICON,
        "raise the credit limit",
        "--tool",
        "api.update_rate_limit",
        "--json",
    ])
    captured = capsys.readouterr()

    # JSON mode must keep the same non-zero exit code as the human-readable path.
    assert exit_code == 2
    payload = json.loads(captured.out)
    assert payload["status"] == "blocked"
    assert payload["tool_name"] == "api.update_rate_limit"


def test_guard_json_allowed_exits_zero(capsys) -> None:
    exit_code = main([
        "guard",
        LEXICON,
        "raise the credit limit",
        "--tool",
        "billing.update_credit_limit",
        "--json",
    ])
    captured = capsys.readouterr()

    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["status"] == "allowed"


def test_match_json_lists_matches(capsys) -> None:
    exit_code = main(["match", LEXICON, "raise the credit limit", "--json"])
    captured = capsys.readouterr()

    assert exit_code == 0
    payload = json.loads(captured.out)
    assert isinstance(payload["matches"], list)
    assert payload["matches"]


def test_invalid_lexicon_error_goes_to_stderr(capsys) -> None:
    exit_code = main(["resolve", "does-not-exist.yaml", "text"])
    captured = capsys.readouterr()

    assert exit_code == 1
    # Diagnostics belong on stderr, not stdout, so they don't pollute a pipe.
    assert captured.out == ""
    assert "Invalid lexicon" in captured.err


def test_bare_invocation_prints_help_to_stderr(capsys) -> None:
    exit_code = main([])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert captured.out == ""
    assert "usage: agent-lexicon" in captured.err
