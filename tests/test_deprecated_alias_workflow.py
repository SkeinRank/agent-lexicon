from __future__ import annotations

from pathlib import Path

from agent_lexicon import (
    Lexicon,
    Term,
    init_workspace,
    load_lexicon,
    publish_local_snapshot,
)
from agent_lexicon.cli import main
from agent_lexicon.scout import lint_working_diff


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_cli_term_deprecate_adds_deprecated_alias(tmp_path: Path) -> None:
    lexicon_path = _write(
        tmp_path / "lexicon" / "lexicon.yaml",
        "version: '1'\n"
        "terms:\n"
        "  - id: core.allowlist\n"
        "    canonical: allowlist\n",
    )

    assert main([
        "term",
        "deprecate",
        "whitelist",
        "--canonical",
        "core.allowlist",
        "--root",
        str(tmp_path),
        "--note",
        "Use inclusive terminology.",
    ]) == 0

    lexicon = load_lexicon(lexicon_path)
    term = lexicon.get_term("core.allowlist")
    assert term is not None
    assert len(term.aliases) == 1
    alias = term.aliases[0]
    assert alias.surface == "whitelist"
    assert alias.deprecated is True
    assert alias.metadata["note"] == "Use inclusive terminology."


def test_cli_term_deprecate_rejects_canonical_surface(tmp_path: Path) -> None:
    _write(
        tmp_path / "lexicon" / "lexicon.yaml",
        "version: '1'\n"
        "terms:\n"
        "  - id: core.allowlist\n"
        "    canonical: allowlist\n"
        "  - id: core.whitelist\n"
        "    canonical: whitelist\n",
    )

    assert main([
        "term",
        "deprecate",
        "whitelist",
        "--canonical",
        "core.allowlist",
        "--root",
        str(tmp_path),
    ]) == 1


def test_publish_deprecate_alias_decision_adds_alias_to_target(tmp_path: Path) -> None:
    base = Lexicon(terms=(Term(id="core.allowlist", canonical="allowlist"),))
    state = init_workspace(tmp_path)
    state.store_candidates([])
    # Store a candidate-like row through the public scout path is unnecessary here:
    # publish only needs a review item, so create a minimal candidate in SQLite via scan tables.
    from agent_lexicon.scout import ScoutCandidate, CandidateSurfaceKind

    candidate = ScoutCandidate(
        surface="whitelist",
        normalized_surface="whitelist",
        kind=CandidateSurfaceKind.IDENTIFIER,
        score=0.9,
        jargon_score=1.0,
        background_penalty=0.0,
        occurrence_count=1,
        document_count=1,
        metadata={},
    )
    state.store_candidates([candidate])
    state.save_review_decision(
        "whitelist",
        "deprecate_alias",
        metadata={"deprecate_alias": {"target_term_id": "core.allowlist"}},
    )

    snapshot = publish_local_snapshot(
        state,
        base_lexicon=base,
        snapshot_id="snapshot_deprecate_alias",
    )

    term = snapshot.lexicon.get_term("core.allowlist")
    assert term is not None
    assert [(alias.surface, alias.deprecated) for alias in term.aliases] == [("whitelist", True)]
    record = snapshot.metadata["published_decisions"][0]
    assert record["decision"] == "deprecate_alias"
    assert record["result"] == "deprecated_alias_added"
    assert record["term_id"] == "core.allowlist"
    assert record["included_in_lexicon"] is True

    diff = (
        "diff --git a/docs/terms.md b/docs/terms.md\n"
        "--- a/docs/terms.md\n"
        "+++ b/docs/terms.md\n"
        "@@ -0,0 +1 @@\n"
        "+Use whitelist here.\n"
    )
    report = lint_working_diff(snapshot.lexicon, diff_text=diff, root=tmp_path)
    assert report.fail_count == 1
    assert report.findings[0].surface == "whitelist"
    assert report.findings[0].canonical == "allowlist"


def test_review_inbox_exposes_deprecate_alias_controls(tmp_path: Path) -> None:
    from agent_lexicon import build_review_inbox_html

    _write(
        tmp_path / "lexicon" / "lexicon.yaml",
        "version: '1'\n"
        "terms:\n"
        "  - id: core.allowlist\n"
        "    canonical: allowlist\n",
    )
    state = init_workspace(tmp_path)
    from agent_lexicon.scout import ScoutCandidate, CandidateSurfaceKind

    candidate = ScoutCandidate(
        surface="whitelist",
        normalized_surface="whitelist",
        kind=CandidateSurfaceKind.IDENTIFIER,
        score=0.9,
        jargon_score=1.0,
        background_penalty=0.0,
        occurrence_count=1,
        document_count=1,
        metadata={},
    )
    state.store_candidates([candidate])
    state.save_review_decision(
        "whitelist",
        "deprecate_alias",
        metadata={"deprecate_alias": {"target_term_id": "core.allowlist"}},
    )

    html = build_review_inbox_html(state, selected_surface="whitelist")
    assert "Deprecate alias" in html
    assert "deprecate_alias" in html
    assert "core.allowlist" in html

def test_review_inbox_deprecate_alias_prompt_keeps_javascript_valid(tmp_path: Path) -> None:
    from agent_lexicon import build_review_inbox_html

    _write(
        tmp_path / "lexicon" / "lexicon.yaml",
        "version: '1'\n"
        "terms:\n"
        "  - id: core.allowlist\n"
        "    canonical: allowlist\n",
    )
    state = init_workspace(tmp_path)
    from agent_lexicon.scout import CandidateSurfaceKind, ScoutCandidate

    state.store_candidates([
        ScoutCandidate(
            surface="whitelist",
            normalized_surface="whitelist",
            kind=CandidateSurfaceKind.IDENTIFIER,
            score=0.9,
            jargon_score=1.0,
            background_penalty=0.0,
            occurrence_count=1,
            document_count=1,
            metadata={},
        )
    ])

    html = build_review_inbox_html(state, selected_surface="whitelist")

    assert "promptText += '\\n\\nSuggestions:\\n' + lines.join('\\n');" in html
    assert "promptText += '\n\nSuggestions:\n'" not in html

