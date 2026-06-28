from __future__ import annotations

from pathlib import Path

from agent_lexicon import Alias, Lexicon, SurfaceKind, Term, build_surface_matcher, find_surface_matches, load_lexicon


EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples" / "customer_limits"


def test_matcher_finds_canonical_and_alias_surfaces() -> None:
    lexicon = load_lexicon(EXAMPLES_DIR / "lexicon.yaml")
    matcher = build_surface_matcher(lexicon)

    matches = matcher.match("The customer cap and rate limit changed.", longest_only=True)

    assert [(match.term_id, match.kind, match.matched_text) for match in matches] == [
        ("billing.credit_limit", SurfaceKind.ALIAS, "customer cap"),
        ("api.rate_limit", SurfaceKind.CANONICAL, "rate limit"),
    ]


def test_matcher_applies_scope_filtering() -> None:
    lexicon = Lexicon(
        terms=(
            Term(
                id="billing.credit_limit",
                canonical="limit",
                scopes=("billing",),
            ),
            Term(
                id="api.rate_limit",
                canonical="limit",
                scopes=("api",),
            ),
        )
    )
    matcher = build_surface_matcher(lexicon)

    billing_matches = matcher.match("increase the limit", scopes=("billing",))
    api_matches = matcher.match("increase the limit", scopes=("api",))
    all_matches = matcher.match("increase the limit")

    assert [match.term_id for match in billing_matches] == ["billing.credit_limit"]
    assert [match.term_id for match in api_matches] == ["api.rate_limit"]
    assert sorted(match.term_id for match in all_matches) == ["api.rate_limit", "billing.credit_limit"]


def test_alias_without_scopes_inherits_term_scopes() -> None:
    lexicon = Lexicon(
        terms=(
            Term(
                id="billing.credit_limit",
                canonical="credit limit",
                scopes=("billing",),
                aliases=(Alias(surface="customer cap", term_id="billing.credit_limit"),),
            ),
        )
    )
    matcher = build_surface_matcher(lexicon)

    assert matcher.match("customer cap", scopes=("api",)) == ()
    assert matcher.match("customer cap", scopes=("billing",))[0].term_id == "billing.credit_limit"


def test_matcher_respects_token_boundaries() -> None:
    lexicon = Lexicon(terms=(Term(id="api.rate_limit", canonical="rate limit"),))
    matcher = build_surface_matcher(lexicon)

    assert matcher.match("crate limit should not match") == ()
    assert matcher.match("rate limit should match")[0].matched_text == "rate limit"


def test_matcher_supports_case_sensitive_aliases() -> None:
    lexicon = Lexicon(
        terms=(
            Term(
                id="runtime.api_key",
                canonical="API key",
                aliases=(Alias(surface="API_KEY", term_id="runtime.api_key", case_sensitive=True),),
            ),
        )
    )
    matcher = build_surface_matcher(lexicon)

    matches = matcher.match("Use API_KEY, not api_key.")

    assert [match.matched_text for match in matches] == ["API_KEY"]
    assert matches[0].case_sensitive is True


def test_matcher_can_exclude_deprecated_aliases() -> None:
    lexicon = Lexicon(
        terms=(
            Term(
                id="docs.binding",
                canonical="binding",
                aliases=(Alias(surface="connector", term_id="docs.binding", deprecated=True),),
            ),
        )
    )
    matcher = build_surface_matcher(lexicon)

    all_matches = matcher.match("connector binding")
    active_matches = matcher.match("connector binding", include_deprecated=False)

    assert [match.surface for match in all_matches] == ["connector", "binding"]
    assert [match.surface for match in active_matches] == ["binding"]


def test_matcher_can_keep_longest_non_overlapping_matches() -> None:
    lexicon = Lexicon(
        terms=(
            Term(id="api.limit", canonical="limit"),
            Term(id="api.rate_limit", canonical="rate limit"),
        )
    )

    matches = find_surface_matches(lexicon, "rate limit", longest_only=True)

    assert [(match.term_id, match.matched_text) for match in matches] == [("api.rate_limit", "rate limit")]


def test_surface_match_serializes_to_dict() -> None:
    lexicon = load_lexicon(EXAMPLES_DIR / "lexicon.yaml")

    match = find_surface_matches(lexicon, "customer cap", scopes=("billing",))[0]

    payload = match.to_dict()
    assert payload["term_id"] == "billing.credit_limit"
    assert payload["kind"] == "alias"
    assert payload["start"] == 0
    assert payload["end"] == 12
