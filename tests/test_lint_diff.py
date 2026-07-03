from __future__ import annotations

from pathlib import Path

from agent_lexicon import load_lexicon
from agent_lexicon.scout import lint_working_diff


def _lexicon(tmp_path: Path) -> Path:
    path = tmp_path / "lexicon.yaml"
    path.write_text(
        "version: '1'\n"
        "scopes:\n  - id: core\n"
        "terms:\n"
        "  - id: core.context_space\n"
        "    canonical: ContextSpace\n"
        "    scopes: [core]\n"
        "    aliases:\n"
        "      - surface: WorkspaceScope\n"
        "        deprecated: true\n",
        encoding="utf-8",
    )
    return path


def _diff(path: str, added: list[str]) -> str:
    body = "".join(f"+{line}\n" for line in added)
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        f"@@ -0,0 +1,{len(added)} @@\n"
        f"{body}"
    )


def test_lint_diff_flags_deprecated_as_fail(tmp_path: Path) -> None:
    lexicon = load_lexicon(_lexicon(tmp_path))
    diff = _diff("notes.md", ["We use WorkspaceScope for isolation."])
    report = lint_working_diff(lexicon, diff_text=diff, root=tmp_path)

    assert report.fail_count == 1
    finding = report.findings[0]
    assert finding.level == 1
    assert finding.severity == "fail"
    assert finding.surface == "WorkspaceScope"
    assert finding.canonical == "ContextSpace"
    assert report.exit_code(strict=False) == 1


def test_lint_diff_near_miss_is_warn_unless_strict(tmp_path: Path) -> None:
    lexicon = load_lexicon(_lexicon(tmp_path))
    diff = _diff("notes.md", ["The ContextSapce value is set here."])
    report = lint_working_diff(lexicon, diff_text=diff, root=tmp_path)

    warns = [f for f in report.findings if f.severity == "warn"]
    assert warns, "expected a near-miss warning"
    assert warns[0].level == 2
    assert warns[0].canonical == "ContextSpace"
    # Warn does not fail by default, but does under strict.
    assert report.exit_code(strict=False) == 0
    assert report.exit_code(strict=True) == 1


def test_lint_diff_unknown_term_is_info_only(tmp_path: Path) -> None:
    lexicon = load_lexicon(_lexicon(tmp_path))
    diff = _diff("notes.md", ["Introduce a TaskMemoryProfile abstraction now."])
    report = lint_working_diff(lexicon, diff_text=diff, root=tmp_path)

    infos = [f for f in report.findings if f.severity == "info"]
    assert infos, "expected an info-level new-term finding"
    assert all(f.level == 3 for f in infos)
    # Info never fails, even under strict.
    assert report.exit_code(strict=False) == 0
    assert report.exit_code(strict=True) == 0


def test_lint_diff_clean_diff_has_no_findings(tmp_path: Path) -> None:
    lexicon = load_lexicon(_lexicon(tmp_path))
    diff = _diff("notes.md", ["We use ContextSpace consistently across the code."])
    report = lint_working_diff(lexicon, diff_text=diff, root=tmp_path)

    assert report.fail_count == 0
    assert report.warn_count == 0
    assert report.exit_code(strict=True) == 0


def test_lint_diff_json_shape(tmp_path: Path) -> None:
    import json
    from agent_lexicon.cli import main

    lex = _lexicon(tmp_path)
    diff = _diff("notes.md", ["We use WorkspaceScope here."])
    # Drive via CLI stdin path is covered separately; here assert the report dict.
    report = lint_working_diff(load_lexicon(lex), diff_text=diff, root=tmp_path)
    payload = json.loads(json.dumps(report.to_dict()))
    assert payload["fail_count"] == 1
    assert payload["findings"][0]["surface"] == "WorkspaceScope"


def test_lint_diff_cli_stdin(tmp_path: Path, monkeypatch, capsys) -> None:
    import io
    from agent_lexicon.cli import main

    # A workspace with a lexicon under the default path.
    lexicon_dir = tmp_path / "lexicon"
    lexicon_dir.mkdir()
    (lexicon_dir / "lexicon.yaml").write_text(
        "version: '1'\nscopes:\n  - id: core\n"
        "terms:\n  - id: core.context_space\n    canonical: ContextSpace\n"
        "    scopes: [core]\n    aliases:\n      - surface: WorkspaceScope\n        deprecated: true\n",
        encoding="utf-8",
    )
    diff = _diff("notes.md", ["We use WorkspaceScope for isolation."])
    monkeypatch.setattr("sys.stdin", io.StringIO(diff))

    code = main(["lint-diff", "--root", str(tmp_path), "--stdin"])
    out = capsys.readouterr().out
    assert code == 1
    assert "Deprecated terms (fail)" in out
    assert "WorkspaceScope" in out
