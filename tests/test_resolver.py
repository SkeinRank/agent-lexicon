from __future__ import annotations

from pathlib import Path

from agent_lexicon import LexiconResolver, ResolutionAction, ResolutionStatus, resolve_text, load_lexicon


EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples" / "customer_limits"


def test_resolver_resolves_single_candidate() -> None:
    lexicon = load_lexicon(EXAMPLES_DIR / "lexicon.yaml")

    decision = resolve_text(lexicon, "increase the customer cap")

    assert decision.status == ResolutionStatus.RESOLVED
    assert decision.action == ResolutionAction.USE_TERMS
    assert decision.primary_term_id == "billing.credit_limit"
    assert decision.candidates[0].canonical == "credit limit"
    assert decision.candidates[0].matched_surfaces == ("customer cap",)


def test_resolver_detects_ambiguity_without_scope() -> None:
    lexicon = load_lexicon(EXAMPLES_DIR / "lexicon.yaml")
    resolver = LexiconResolver.from_lexicon(lexicon)

    decision = resolver.resolve("increase the limit")

    assert decision.status == ResolutionStatus.AMBIGUOUS
    assert decision.action == ResolutionAction.ASK_CLARIFICATION
    assert decision.primary_term_id is None
    assert sorted(candidate.term_id for candidate in decision.candidates) == [
        "api.rate_limit",
        "billing.credit_limit",
    ]
    assert {match.matched_text for match in decision.matches} == {"limit"}


def test_resolver_uses_scope_to_select_candidate() -> None:
    lexicon = load_lexicon(EXAMPLES_DIR / "lexicon.yaml")

    billing_decision = resolve_text(lexicon, "increase the limit", scopes=("billing",))
    api_decision = resolve_text(lexicon, "increase the limit", scopes=("api",))

    assert billing_decision.status == ResolutionStatus.RESOLVED
    assert billing_decision.primary_term_id == "billing.credit_limit"
    assert api_decision.status == ResolutionStatus.RESOLVED
    assert api_decision.primary_term_id == "api.rate_limit"


def test_resolver_prefers_longer_surface_over_nested_alias() -> None:
    lexicon = load_lexicon(EXAMPLES_DIR / "lexicon.yaml")

    decision = resolve_text(lexicon, "the rate limit changed")

    assert decision.status == ResolutionStatus.RESOLVED
    assert decision.primary_term_id == "api.rate_limit"
    assert [match.matched_text for match in decision.matches] == ["rate limit"]


def test_resolver_returns_unknown_when_no_surfaces_match() -> None:
    lexicon = load_lexicon(EXAMPLES_DIR / "lexicon.yaml")

    decision = resolve_text(lexicon, "change the retention policy")

    assert decision.status == ResolutionStatus.UNKNOWN
    assert decision.action == ResolutionAction.NO_MATCH
    assert decision.candidates == ()
    assert decision.matches == ()


def test_resolution_decision_serializes_to_dict() -> None:
    lexicon = load_lexicon(EXAMPLES_DIR / "lexicon.yaml")

    payload = resolve_text(lexicon, "increase the customer cap").to_dict()

    assert payload["status"] == "resolved"
    assert payload["action"] == "use_terms"
    assert payload["primary_term_id"] == "billing.credit_limit"
    assert payload["candidates"][0]["term_id"] == "billing.credit_limit"
    assert payload["matches"][0]["matched_text"] == "customer cap"
