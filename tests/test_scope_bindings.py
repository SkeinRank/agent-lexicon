from __future__ import annotations

import pytest

from agent_lexicon import Alias, Lexicon, Scope, Term
from agent_lexicon.config import AgentLexiconConfig, AgentLexiconConfigError, ScopeBinding, validate_scope_bindings
from agent_lexicon.scout.git_merge import GitDiffAddedLine, build_git_merge_terminology_report
from agent_lexicon.scout.lint_diff import lint_working_diff


def _diff(path: str, added: list[str]) -> str:
    body = "".join(f"+{line}\n" for line in added)
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        f"@@ -0,0 +1,{len(added)} @@\n"
        f"{body}"
    )


def _scoped_lexicon() -> Lexicon:
    return Lexicon(
        scopes=(Scope(id="cluster"), Scope(id="numa")),
        terms=(
            Term(
                id="cluster.primary",
                canonical="primary",
                scopes=("cluster",),
                aliases=(Alias(surface="master", term_id="cluster.primary", deprecated=True),),
            ),
            Term(id="cluster.control_plane", canonical="control plane", scopes=("cluster",)),
            Term(id="numa.node_topology", canonical="node topology", scopes=("numa",)),
        ),
    )


def test_scope_binding_suppresses_deprecated_alias_outside_active_scope(tmp_path) -> None:
    lexicon = _scoped_lexicon()
    diff = _diff("pkg/kubelet/topology.py", ["master = select_node()"])
    report = lint_working_diff(
        lexicon,
        diff_text=diff,
        root=tmp_path,
        scope_bindings=(ScopeBinding(paths=("pkg/kubelet/**",), scopes=("numa",)),),
    )

    assert report.fail_count == 0


def test_scope_binding_allows_deprecated_alias_in_matching_scope(tmp_path) -> None:
    lexicon = _scoped_lexicon()
    diff = _diff("pkg/controller/legacy.py", ["master = select_node()"])
    report = lint_working_diff(
        lexicon,
        diff_text=diff,
        root=tmp_path,
        scope_bindings=(ScopeBinding(paths=("pkg/kubelet/**",), scopes=("numa",)),),
    )

    assert report.fail_count == 1
    assert report.findings[0].surface == "master"


def test_scope_binding_first_match_wins_for_overlapping_patterns() -> None:
    lexicon = _scoped_lexicon()
    lines = (GitDiffAddedLine(path="pkg/controller/legacy.py", line_number=3, text="master = select_node()"),)

    report = build_git_merge_terminology_report(
        lexicon,
        lines,
        scope_bindings=(
            ScopeBinding(paths=("**",), scopes=("numa",)),
            ScopeBinding(paths=("pkg/controller/**",), scopes=("cluster",)),
        ),
    )

    assert report.known_occurrence_count == 0


def test_scope_binding_limits_near_miss_targets() -> None:
    lexicon = _scoped_lexicon()
    lines = (GitDiffAddedLine(path="pkg/kubelet/topology.py", line_number=4, text="controlPlanee = True"),)

    report = build_git_merge_terminology_report(
        lexicon,
        lines,
        scope_bindings=(ScopeBinding(paths=("pkg/kubelet/**",), scopes=("numa",)),),
        min_confidence=0.30,
    )

    targets = {
        suggestion.target_term_id
        for identifier in report.needs_review
        for suggestion in identifier.suggestions
    }
    assert "cluster.control_plane" not in targets


def test_invalid_scope_binding_scope_is_rejected() -> None:
    config = AgentLexiconConfig(
        scope_bindings=(ScopeBinding(paths=("pkg/kubelet/**",), scopes=("missing",)),)
    )

    with pytest.raises(AgentLexiconConfigError, match="unknown scope"):
        validate_scope_bindings(config, known_scope_ids=("cluster", "numa"))
