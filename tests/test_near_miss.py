from __future__ import annotations

import json
from pathlib import Path

from agent_lexicon import (
    Alias,
    Lexicon,
    NearMissReason,
    ResolutionStatus,
    Term,
    discover_unknown_identifier_surfaces,
    resolve_text,
    suggest_near_misses,
)
from agent_lexicon.cli import main


def test_near_miss_suggests_known_token_target_for_unknown_identifier() -> None:
    lexicon = Lexicon(
        terms=(
            Term(id="auth.access_token", canonical="access token", scopes=("auth",)),
        )
    )

    report = suggest_near_misses(lexicon, "authToken", scopes=("auth",))

    assert report.suggestion_count == 1
    suggestion = report.suggestions[0]
    assert suggestion.target_term_id == "auth.access_token"
    assert suggestion.target_canonical == "access token"
    assert suggestion.confidence >= 0.42
    assert NearMissReason.SHARED_FRAGMENT in suggestion.reasons
    assert "token" in suggestion.shared_fragments


def test_near_miss_uses_alias_and_scope_filtering() -> None:
    lexicon = Lexicon(
        terms=(
            Term(
                id="billing.credit_limit",
                canonical="credit limit",
                scopes=("billing",),
                aliases=(Alias(surface="customer cap", term_id="billing.credit_limit"),),
            ),
            Term(id="api.rate_limit", canonical="rate limit", scopes=("api",)),
        )
    )

    billing_report = suggest_near_misses(lexicon, "accountCap", scopes=("billing",))
    api_report = suggest_near_misses(lexicon, "accountCap", scopes=("api",))

    assert billing_report.suggestions[0].target_term_id == "billing.credit_limit"
    assert all(suggestion.target_term_id != "billing.credit_limit" for suggestion in api_report.suggestions)


def test_resolver_attaches_near_miss_metadata_only_for_unknown_identifiers() -> None:
    lexicon = Lexicon(
        terms=(
            Term(id="auth.access_token", canonical="access token", scopes=("auth",)),
        )
    )

    decision = resolve_text(lexicon, "rotate authToken", scopes=("auth",))

    assert decision.status == ResolutionStatus.UNKNOWN
    assert decision.metadata["unknown_identifier_surfaces"] == ["authToken"]
    assert decision.metadata["near_miss_suggestions"][0]["suggestions"][0]["target_term_id"] == "auth.access_token"

    noisy = resolve_text(lexicon, "PaymentCoreV2", scopes=("auth",))
    assert noisy.status == ResolutionStatus.UNKNOWN
    assert noisy.metadata["unknown_identifier_surfaces"] == ["PaymentCoreV2"]
    assert "near_miss_suggestions" not in noisy.metadata


def test_identifier_surface_extraction_is_stable() -> None:
    surfaces = discover_unknown_identifier_surfaces("Use `authToken` with session_key and PaymentCoreV2")

    assert surfaces == ("authToken", "session_key", "PaymentCoreV2")


def test_cli_resolve_prints_near_miss_suggestions(tmp_path: Path, capsys) -> None:
    path = tmp_path / "lexicon.yaml"
    path.write_text(
        """
        version: 1
        scopes:
          - id: auth
        terms:
          - id: auth.access_token
            canonical: access token
            scopes: [auth]
        """,
        encoding="utf-8",
    )

    exit_code = main(["resolve", str(path), "rotate authToken", "--scope", "auth"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Status: unknown" in captured.out
    assert "Near-miss suggestions:" in captured.out
    assert "auth.access_token" in captured.out


def test_near_miss_report_serializes_to_json() -> None:
    lexicon = Lexicon(terms=(Term(id="auth.access_token", canonical="access token"),))
    report = suggest_near_misses(lexicon, "authToken")

    payload = json.loads(json.dumps(report.to_dict()))

    assert payload["suggestion_count"] == 1
    assert payload["suggestions"][0]["target_term_id"] == "auth.access_token"
