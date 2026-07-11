from __future__ import annotations

import io
import json
from pathlib import Path

from agent_lexicon.cli import main


def _write_project(tmp_path: Path) -> None:
    lexicon_dir = tmp_path / "lexicon"
    lexicon_dir.mkdir()
    (lexicon_dir / "lexicon.yaml").write_text(
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
    (tmp_path / "notes.md").write_text("WorkspaceScope is legacy.\n", encoding="utf-8")


def _diff(path: str, added: list[str]) -> str:
    body = "".join(f"+{line}\n" for line in added)
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        f"@@ -1,0 +2,{len(added)} @@\n"
        f"{body}"
    )


def test_baseline_create_writes_current_findings(tmp_path: Path, capsys) -> None:
    _write_project(tmp_path)

    code = main(["baseline", "create", "--root", str(tmp_path)])
    out = capsys.readouterr().out

    assert code == 0
    assert "Baseline written:" in out
    path = tmp_path / "lexicon" / "baseline.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "baseline-v1"
    assert payload["entries"]
    assert payload["entries"][0]["kind"] == "deprecated"


def test_lint_diff_baseline_suppresses_existing_debt(tmp_path: Path, monkeypatch, capsys) -> None:
    _write_project(tmp_path)
    assert main(["baseline", "create", "--root", str(tmp_path)]) == 0
    capsys.readouterr()
    monkeypatch.setattr("sys.stdin", io.StringIO(_diff("notes.md", ["WorkspaceScope is legacy."])))

    code = main(["lint-diff", "--root", str(tmp_path), "--stdin"])
    out = capsys.readouterr().out

    assert code == 0
    assert "Baseline:" in out
    assert "suppressed=1" in out
    assert "Deprecated terms" not in out


def test_lint_diff_baseline_fails_when_legacy_file_grows(tmp_path: Path, monkeypatch, capsys) -> None:
    _write_project(tmp_path)
    assert main(["baseline", "create", "--root", str(tmp_path)]) == 0
    capsys.readouterr()
    (tmp_path / "notes.md").write_text(
        "WorkspaceScope is legacy.\nAnother WorkspaceScope was added.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(_diff("notes.md", ["Another WorkspaceScope was added."])))

    code = main(["lint-diff", "--root", str(tmp_path), "--stdin"])
    out = capsys.readouterr().out

    assert code == 1
    assert "new violations=1" in out
    assert "Deprecated terms" in out


def test_baseline_update_rejects_growth_without_allow_growth(tmp_path: Path, capsys) -> None:
    _write_project(tmp_path)
    assert main(["baseline", "create", "--root", str(tmp_path)]) == 0
    capsys.readouterr()
    (tmp_path / "notes.md").write_text(
        "WorkspaceScope is legacy.\nAnother WorkspaceScope was added.\n",
        encoding="utf-8",
    )

    code = main(["baseline", "update", "--root", str(tmp_path)])
    captured = capsys.readouterr()

    assert code == 1
    assert "new violations: 1" in captured.err


def test_baseline_update_records_progress(tmp_path: Path, capsys) -> None:
    _write_project(tmp_path)
    assert main(["baseline", "create", "--root", str(tmp_path)]) == 0
    capsys.readouterr()
    (tmp_path / "notes.md").write_text("ContextSpace is current.\n", encoding="utf-8")

    code = main(["baseline", "update", "--root", str(tmp_path)])
    out = capsys.readouterr().out

    assert code == 0
    assert "baseline:" in out
    payload = json.loads((tmp_path / "lexicon" / "baseline.json").read_text(encoding="utf-8"))
    assert payload["entries"] == []
