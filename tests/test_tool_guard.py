from __future__ import annotations

from pathlib import Path

from agent_lexicon import (
    Lexicon,
    Term,
    ToolGuard,
    ToolGuardAction,
    ToolGuardStatus,
    guard_tool_call,
    load_lexicon,
)


EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples" / "customer_limits"


def test_tool_guard_blocks_ambiguous_tool_call_without_scope() -> None:
    lexicon = load_lexicon(EXAMPLES_DIR / "lexicon.yaml")

    decision = guard_tool_call(
        lexicon,
        "increase the limit",
        tool_name="api.update_rate_limit",
    )

    assert decision.status == ToolGuardStatus.NEEDS_CLARIFICATION
    assert decision.action == ToolGuardAction.ASK_CLARIFICATION
    assert decision.is_allowed is False
    assert sorted(decision.matched_term_ids) == ["api.rate_limit", "billing.credit_limit"]
    assert decision.resolution.status.value == "ambiguous"


def test_tool_guard_allows_matching_tool_for_resolved_term() -> None:
    lexicon = load_lexicon(EXAMPLES_DIR / "lexicon.yaml")

    decision = guard_tool_call(
        lexicon,
        "increase the customer cap",
        tool_name="billing.update_credit_limit",
    )

    assert decision.status == ToolGuardStatus.ALLOWED
    assert decision.action == ToolGuardAction.PROCEED
    assert decision.is_allowed is True
    assert decision.matched_term_ids == ("billing.credit_limit",)
    assert decision.allowed_tool_names == ("billing.update_credit_limit",)


def test_tool_guard_blocks_tool_mismatch_for_resolved_term() -> None:
    lexicon = load_lexicon(EXAMPLES_DIR / "lexicon.yaml")

    decision = ToolGuard.from_lexicon(lexicon).guard(
        "increase the customer cap",
        tool_name="api.update_rate_limit",
    )

    assert decision.status == ToolGuardStatus.BLOCKED
    assert decision.action == ToolGuardAction.BLOCK
    assert decision.is_allowed is False
    assert decision.allowed_tool_names == ("billing.update_credit_limit",)
    assert decision.to_dict()["status"] == "blocked"


def test_tool_guard_uses_scope_to_allow_specific_ambiguous_surface() -> None:
    lexicon = load_lexicon(EXAMPLES_DIR / "lexicon.yaml")

    decision = guard_tool_call(
        lexicon,
        "increase the limit",
        tool_name="billing.update_credit_limit",
        scopes=("billing",),
    )

    assert decision.status == ToolGuardStatus.ALLOWED
    assert decision.matched_term_ids == ("billing.credit_limit",)


def test_tool_guard_allows_unrestricted_resolved_term() -> None:
    lexicon = Lexicon(terms=(Term(id="docs.snapshot", canonical="snapshot"),))

    decision = guard_tool_call(
        lexicon,
        "publish the snapshot",
        tool_name="docs.publish_snapshot",
    )

    assert decision.status == ToolGuardStatus.ALLOWED
    assert decision.allowed_tool_names == ()
    assert decision.reason == "Resolved terminology has no tool restrictions."


def test_tool_guard_no_match_is_non_blocking() -> None:
    lexicon = load_lexicon(EXAMPLES_DIR / "lexicon.yaml")

    decision = guard_tool_call(
        lexicon,
        "send a notification",
        tool_name="notifications.send",
    )

    assert decision.status == ToolGuardStatus.NO_MATCH
    assert decision.action == ToolGuardAction.PROCEED
    assert decision.is_allowed is True
