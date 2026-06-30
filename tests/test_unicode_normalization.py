from __future__ import annotations

from agent_lexicon import (
    Lexicon,
    Term,
    ToolGuardStatus,
    build_surface_matcher,
    guard_tool_call,
    normalize_text_for_matching,
    resolve_text,
)


def test_unicode_normalizer_handles_spaces_fullwidth_and_bidi() -> None:
    result = normalize_text_for_matching("ａｃｃｅｓｓ\u00a0token\u202e")

    assert result.normalized_text == "access token"
    assert result.changed is True
    assert result.has_bidi_control is True
    assert [finding.kind.value for finding in result.findings] == [
        "compatibility",
        "compatibility",
        "compatibility",
        "compatibility",
        "compatibility",
        "compatibility",
        "non_ascii_space",
        "bidi_control",
    ]


def test_matcher_resolves_zero_width_separator_between_identifier_fragments() -> None:
    lexicon = Lexicon(terms=(Term(id="auth.access_token", canonical="access token"),))
    matcher = build_surface_matcher(lexicon)

    matches = matcher.match("rotate access\u200btoken now", longest_only=True)

    assert [(match.term_id, match.surface, match.matched_text) for match in matches] == [
        ("auth.access_token", "access token", "access\u200btoken")
    ]


def test_resolver_reports_unicode_metadata_for_normalized_input() -> None:
    lexicon = Lexicon(terms=(Term(id="auth.access_token", canonical="access token"),))

    decision = resolve_text(lexicon, "rotate ａｃｃｅｓｓToken")

    assert decision.primary_term_id == "auth.access_token"
    assert decision.metadata["unicode_normalized"] is True
    assert decision.metadata["unicode_findings"]


def test_tool_guard_blocks_bidi_control_by_default() -> None:
    lexicon = Lexicon(
        terms=(
            Term(
                id="auth.access_token",
                canonical="access token",
                tools=("auth.rotate_access_token",),
            ),
        )
    )

    decision = guard_tool_call(
        lexicon,
        "rotate access\u202etoken",
        tool_name="auth.rotate_access_token",
    )

    assert decision.status == ToolGuardStatus.BLOCKED
    assert decision.is_allowed is False
    assert decision.metadata["unicode_blocked"] is True
    assert decision.metadata["unicode_block_reason"] == "bidi_control"


def test_tool_guard_can_allow_bidi_control_when_caller_accepts_risk() -> None:
    lexicon = Lexicon(
        terms=(
            Term(
                id="auth.access_token",
                canonical="access token",
                tools=("auth.rotate_access_token",),
            ),
        )
    )

    decision = guard_tool_call(
        lexicon,
        "rotate access\u202etoken",
        tool_name="auth.rotate_access_token",
        block_on_unicode_risk=False,
    )

    assert decision.status == ToolGuardStatus.ALLOWED
    assert decision.is_allowed is True
    assert decision.metadata["unicode_has_bidi_control"] is True
    assert "unicode_blocked" not in decision.metadata
