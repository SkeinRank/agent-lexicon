from __future__ import annotations

from pathlib import Path

from agent_lexicon.cli import main


EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples" / "customer_limits"


def test_cli_check_reports_behavior_metrics(capsys) -> None:
    exit_code = main([
        "check",
        str(EXAMPLES_DIR / "lexicon.yaml"),
        str(EXAMPLES_DIR / "queries.jsonl"),
    ])

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Behavior check:" in captured.out
    assert "Ambiguity detection: 100.0%" in captured.out
    assert "Wrong tool prevention: 100.0%" in captured.out


def test_cli_check_can_emit_json(capsys) -> None:
    exit_code = main([
        "check",
        str(EXAMPLES_DIR / "lexicon.yaml"),
        str(EXAMPLES_DIR / "queries.jsonl"),
        "--json",
    ])

    captured = capsys.readouterr()

    assert exit_code == 0
    assert '"passed": true' in captured.out
    assert '"wrong_tool_prevention_rate": 1.0' in captured.out
