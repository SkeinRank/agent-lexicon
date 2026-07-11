from __future__ import annotations

from pathlib import Path

from agent_lexicon import GitDiffAddedLine, Lexicon, Term, build_git_merge_terminology_report
from agent_lexicon.cli import main


def test_check_merge_text_groups_repeated_review_items() -> None:
    lexicon = Lexicon(terms=(Term(id="core.task_instance", canonical="TaskInstance"),))
    lines = tuple(
        GitDiffAddedLine(path="src/tasks.py", line_number=line, text="ti_state = load_state()")
        for line in range(10, 16)
    )

    report = build_git_merge_terminology_report(lexicon, lines, min_confidence=0.1)
    text = report.to_text()

    assert text.count("'ti_state'") == 1
    assert "×6" in text
    assert "(+3 more)" in text


def test_check_merge_flat_text_keeps_old_style_items() -> None:
    lexicon = Lexicon(terms=(Term(id="core.task_instance", canonical="TaskInstance"),))
    lines = tuple(
        GitDiffAddedLine(path="src/tasks.py", line_number=line, text="ti_state = load_state()")
        for line in range(10, 12)
    )

    report = build_git_merge_terminology_report(lexicon, lines, min_confidence=0.1)
    text = report.to_text(grouped=False)

    assert text.count("'ti_state'") == 2
    assert "×2" not in text


def test_check_merge_cold_start_hides_long_new_term_list_by_default() -> None:
    lexicon = Lexicon(terms=())
    lines = tuple(
        GitDiffAddedLine(path="src/domain.py", line_number=i + 1, text=f"DomainTerm{i} = value")
        for i in range(60)
    )

    report = build_git_merge_terminology_report(lexicon, lines)
    short_text = report.to_text()
    full_text = report.to_text(full_report=True)
    payload = report.to_dict()

    assert payload["cold_start"] is True
    assert "Cold start:" in short_text
    assert "--full-report" in short_text
    assert short_text.count("DomainTerm") < full_text.count("DomainTerm")


def test_scan_warns_when_default_roots_are_missing_in_large_repo(tmp_path: Path, capsys) -> None:
    for i in range(25):
        (tmp_path / f"module_{i}.py").write_text(f"VALUE_{i} = {i}\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Project\n", encoding="utf-8")

    code = main(["scan", "--root", str(tmp_path), "--max-candidates", "5"])
    captured = capsys.readouterr()

    assert code == 0
    assert "Warning: scanned" in captured.err
    assert "Try: agent-lexicon scan <paths>" in captured.err
