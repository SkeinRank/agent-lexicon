from __future__ import annotations

from pathlib import Path

from agent_lexicon.cli import (
    _existing_default_scan_paths,
    _nearby_scan_targets,
    _scan_hint_for_root,
    main,
)


def _make_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    for name, content in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return tmp_path


def test_scan_hint_uses_default_paths_when_present(tmp_path: Path) -> None:
    _make_repo(tmp_path, {"README.md": "# hi", "src/app.py": "x = 1\n"})
    assert _existing_default_scan_paths(tmp_path) == ["README.md", "src"]
    assert _scan_hint_for_root(tmp_path) == "agent-lexicon scan"


def test_scan_hint_falls_back_to_nearby_files(tmp_path: Path) -> None:
    _make_repo(tmp_path, {"api.py": "def f():\n    return 1\n"})
    assert _existing_default_scan_paths(tmp_path) == []
    assert "api.py" in _nearby_scan_targets(tmp_path)
    assert _scan_hint_for_root(tmp_path) == "agent-lexicon scan api.py"


def test_scan_hint_placeholder_when_nothing_scannable(tmp_path: Path) -> None:
    _make_repo(tmp_path, {"data.bin": "\x00\x01"})
    assert _scan_hint_for_root(tmp_path) == "agent-lexicon scan <files or directories>"


def test_init_hint_reflects_repo_contents(tmp_path: Path, capsys) -> None:
    _make_repo(tmp_path, {"api.py": "def f():\n    return 1\n"})
    exit_code = main(["init", "--root", str(tmp_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Next: agent-lexicon scan api.py" in captured.out


def test_scan_without_paths_gives_actionable_message(tmp_path: Path, capsys) -> None:
    _make_repo(tmp_path, {"api.py": "def f():\n    return 1\n"})
    main(["init", "--root", str(tmp_path)])
    capsys.readouterr()

    exit_code = main(["scan", "--root", str(tmp_path)])
    captured = capsys.readouterr()

    assert exit_code == 1
    # The actionable hint must be on stderr, and must name the real file.
    assert "Nothing to scan" in captured.err
    assert "agent-lexicon scan api.py" in captured.err
    assert captured.out == ""


def test_analyze_default_is_human_readable(tmp_path: Path, capsys) -> None:
    _make_repo(tmp_path, {"api.py": "def get_access_token(user):\n    return user.authToken\n"})
    main(["init", "--root", str(tmp_path)])
    main(["scan", "api.py", "--root", str(tmp_path)])
    capsys.readouterr()

    assert main(["analyze", "--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "priority)" in out          # plain-language priority label
    assert "oov=" not in out           # no raw score fields by default
    assert "use --verbose" in out      # discoverability hint


def test_analyze_verbose_shows_scores(tmp_path: Path, capsys) -> None:
    _make_repo(tmp_path, {"api.py": "def get_access_token(user):\n    return user.authToken\n"})
    main(["init", "--root", str(tmp_path)])
    main(["scan", "api.py", "--root", str(tmp_path)])
    capsys.readouterr()

    assert main(["analyze", "--root", str(tmp_path), "--verbose"]) == 0
    out = capsys.readouterr().out
    assert "oov=" in out
    assert "cluster=" in out
    assert "priority=" in out
