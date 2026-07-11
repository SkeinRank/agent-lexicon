"""Tests for deterministic line-context classification and its scoring effect."""

from __future__ import annotations

import hashlib

from agent_lexicon.ingest import IngestDocument
from agent_lexicon.ingest.local import IngestSourceKind
from agent_lexicon.scout.candidates import discover_scout_candidates
from agent_lexicon.scout.line_context import (
    LINE_CONTEXT_VERSION,
    DocumentContextClassifier,
    LineContext,
)


def _classify_lines(relative_path: str, text: str) -> list[LineContext]:
    classifier = DocumentContextClassifier(relative_path=relative_path)
    return [classifier.classify(line.strip()) for line in text.splitlines() if line.strip()]


def _document(relative_path: str, text: str) -> IngestDocument:
    return IngestDocument(
        source_path=f"/repo/{relative_path}",
        relative_path=relative_path,
        text=text,
        kind=IngestSourceKind.MARKDOWN if relative_path.endswith(".md") else IngestSourceKind.PYTHON,
        size_bytes=len(text.encode("utf-8")),
        line_count=len(text.splitlines()),
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def test_prose_documents_classify_entirely_as_prose() -> None:
    contexts = _classify_lines("docs/guide.md", "# Title\n\nThe RuntimeSnapshot holds state.\n")
    assert contexts == [LineContext.PROSE, LineContext.PROSE]


def test_python_comment_docstring_decorator_and_code() -> None:
    text = (
        "# RuntimeSnapshot is the frozen state\n"
        '"""Module docstring about RuntimeSnapshot.\n'
        "Continues here.\n"
        '"""\n'
        "@register_handler\n"
        "class RuntimeSnapshot:\n"
        '"single line string"\n'
    )
    contexts = _classify_lines("src/core.py", text)
    assert contexts == [
        LineContext.COMMENT,
        LineContext.DOCSTRING,
        LineContext.DOCSTRING,
        LineContext.DOCSTRING,
        LineContext.DECORATOR,
        LineContext.CODE,
        LineContext.STRING,
    ]


def test_python_single_line_docstring_does_not_leak_state() -> None:
    text = '"""One-line docstring."""\nclass After:\n'
    contexts = _classify_lines("src/mod.py", text)
    assert contexts == [LineContext.DOCSTRING, LineContext.CODE]


def test_c_style_block_comment_spans_lines() -> None:
    text = "/* block start\nstill inside\nend here */\nint x = 1;\n"
    contexts = _classify_lines("src/main.go", text)
    assert contexts == [
        LineContext.COMMENT,
        LineContext.COMMENT,
        LineContext.COMMENT,
        LineContext.CODE,
    ]


def test_slash_and_sql_and_html_comments() -> None:
    assert _classify_lines("a.ts", "// note\n")[0] is LineContext.COMMENT
    assert _classify_lines("a.sql", "-- note\n")[0] is LineContext.COMMENT
    assert _classify_lines("a.html", "<!-- note -->\n")[0] is LineContext.COMMENT


def test_classifier_is_deterministic() -> None:
    text = '# c\n"""d\n"""\ncode()\n'
    first = _classify_lines("src/x.py", text)
    second = _classify_lines("src/x.py", text)
    assert first == second


def test_prose_plus_code_candidate_outranks_code_only_twin() -> None:
    """Two surfaces with identical shape and frequency; the one that also
    appears in prose (docstring + markdown) must rank higher."""
    code = _document(
        "src/core.py",
        '"""The FrozzleBundle groups related frozzle records."""\n'
        "class FrozzleBundle:\n"
        "    pass\n"
        "class GrizzleBundle:\n"
        "    pass\n"
        "value = GrizzleBundle()\n",
    )
    docs = _document("docs/guide.md", "A FrozzleBundle is the unit of export.\n")
    report = discover_scout_candidates([code, docs], min_score=0.1, max_candidates=20)
    by_surface = {candidate.normalized_surface: candidate for candidate in report.candidates}
    assert "frozzlebundle" in by_surface and "grizzlebundle" in by_surface
    prose_and_code = by_surface["frozzlebundle"]
    code_only = by_surface["grizzlebundle"]
    assert prose_and_code.metadata["score_breakdown"]["context_adjustment"] > 0
    assert code_only.metadata["score_breakdown"]["context_adjustment"] == 0
    assert prose_and_code.score > code_only.score


def test_occurrences_carry_context_tags_and_report_carries_version() -> None:
    code = _document(
        "src/core.py",
        "# WobbleRegistry keeps wobble handlers\nclass WobbleRegistry:\n    pass\n",
    )
    report = discover_scout_candidates([code], min_score=0.1, max_candidates=10)
    assert report.metadata["line_context_version"] == LINE_CONTEXT_VERSION
    target = next(c for c in report.candidates if c.normalized_surface == "wobbleregistry")
    contexts = {occurrence.context for occurrence in target.occurrences}
    assert contexts == {"comment", "code"}
    assert target.metadata["context_counts"] == {"code": 1, "comment": 1}
    assert "context" in target.occurrences[0].to_dict()
