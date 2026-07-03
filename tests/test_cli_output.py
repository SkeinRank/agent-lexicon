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


def _write_context_lexicon(tmp_path: Path) -> Path:
    path = tmp_path / "lex.yaml"
    path.write_text(
        "version: '1'\n"
        "scopes:\n"
        "  - id: core\n"
        "terms:\n"
        "  - id: core.context_space\n"
        "    canonical: ContextSpace\n"
        "    scopes: [core]\n"
        "    aliases:\n"
        "      - surface: WorkspaceScope\n"
        "        deprecated: true\n"
        "  - id: core.legacy\n"
        "    canonical: LegacyThing\n"
        "    scopes: [core]\n"
        "    deprecated: true\n",
        encoding="utf-8",
    )
    return path


def test_context_command_human_output(tmp_path: Path, capsys) -> None:
    path = _write_context_lexicon(tmp_path)
    assert main(["context", str(path)]) == 0
    out = capsys.readouterr().out
    assert "Use these canonical terms:" in out
    assert "ContextSpace" in out
    assert "Avoid:" in out
    assert 'WorkspaceScope (use "ContextSpace" instead)' in out
    assert "LegacyThing (deprecated term)" in out


def test_context_command_json_output(tmp_path: Path, capsys) -> None:
    path = _write_context_lexicon(tmp_path)
    assert main(["context", str(path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    canon = {t["canonical"] for t in payload["use"]}
    assert "ContextSpace" in canon
    assert "LegacyThing" not in canon  # deprecated terms are not "use"
    avoid = {a["surface"] for a in payload["avoid"]}
    assert "WorkspaceScope" in avoid
    assert "LegacyThing" in avoid


def test_context_command_bad_lexicon(tmp_path: Path, capsys) -> None:
    assert main(["context", str(tmp_path / "missing.yaml")]) == 1
    assert "Invalid lexicon" in capsys.readouterr().err


def _init_git_repo(tmp_path: Path):
    import subprocess

    def run(*args):
        subprocess.run(args, cwd=tmp_path, check=True, capture_output=True)

    run("git", "init", "-q")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "t")
    return run


def test_check_merge_semantic_check_flags_deprecated_alias(tmp_path: Path, capsys) -> None:
    run = _init_git_repo(tmp_path)
    lexicon_dir = tmp_path / "lexicon"
    lexicon_dir.mkdir()
    (lexicon_dir / "lexicon.yaml").write_text(
        "version: '1'\n"
        "scopes:\n  - id: core\n"
        "terms:\n"
        "  - id: core.credit_limit\n"
        "    canonical: credit limit\n"
        "    scopes: [core]\n"
        "    aliases:\n"
        "      - surface: customer cap\n"
        "        deprecated: true\n",
        encoding="utf-8",
    )
    (tmp_path / "notes.md").write_text("# start\n", encoding="utf-8")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "init")
    (tmp_path / "notes.md").write_text("We raise the customer cap after verification.\n", encoding="utf-8")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "change")

    code = main([
        "check-merge", "--root", str(tmp_path),
        "--lexicon", str(lexicon_dir / "lexicon.yaml"),
        "--base", "HEAD~1", "--head", "HEAD",
        "--semantic-check",
    ])
    out = capsys.readouterr().out
    assert code == 1
    assert "Semantic conflicts detected" in out
    assert "customer cap vs credit limit" in out


def test_check_merge_semantic_check_clean(tmp_path: Path, capsys) -> None:
    run = _init_git_repo(tmp_path)
    lexicon_dir = tmp_path / "lexicon"
    lexicon_dir.mkdir()
    (lexicon_dir / "lexicon.yaml").write_text(
        "version: '1'\nscopes:\n  - id: core\nterms:\n"
        "  - id: core.credit_limit\n    canonical: credit limit\n    scopes: [core]\n",
        encoding="utf-8",
    )
    (tmp_path / "notes.md").write_text("# start\n", encoding="utf-8")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "init")
    (tmp_path / "notes.md").write_text("We raise the credit limit after verification.\n", encoding="utf-8")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "change")

    code = main([
        "check-merge", "--root", str(tmp_path),
        "--lexicon", str(lexicon_dir / "lexicon.yaml"),
        "--base", "HEAD~1", "--head", "HEAD",
        "--semantic-check",
    ])
    out = capsys.readouterr().out
    assert code == 0
    assert "No semantic conflicts found." in out
