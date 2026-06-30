from __future__ import annotations

from dataclasses import dataclass

from agent_lexicon import Alias, Lexicon, Term, suggest_near_misses


@dataclass(frozen=True)
class NearMissEvalCase:
    surface: str
    expected_target_id: str | None


def _eval_lexicon() -> Lexicon:
    return Lexicon(
        terms=(
            Term(
                id="auth.access_token",
                canonical="access token",
                scopes=("auth",),
                aliases=(
                    Alias(surface="bearer token", term_id="auth.access_token"),
                    Alias(surface="credential token", term_id="auth.access_token"),
                ),
            ),
            Term(
                id="data.partition_key",
                canonical="partition key",
                scopes=("data",),
                aliases=(
                    Alias(surface="shard key", term_id="data.partition_key"),
                    Alias(surface="row identifier", term_id="data.partition_key"),
                ),
            ),
            Term(
                id="billing.credit_limit",
                canonical="credit limit",
                scopes=("billing",),
                aliases=(Alias(surface="customer cap", term_id="billing.credit_limit"),),
            ),
        )
    )


def _top_target(lexicon: Lexicon, surface: str) -> str | None:
    report = suggest_near_misses(lexicon, surface, max_suggestions=1)
    if not report.suggestions:
        return None
    return report.suggestions[0].target_term_id


def test_near_miss_eval_keeps_recall_on_common_identifier_drift() -> None:
    lexicon = _eval_lexicon()
    cases = (
        NearMissEvalCase("authToken", "auth.access_token"),
        NearMissEvalCase("accessTok", "auth.access_token"),
        NearMissEvalCase("credentialToken", "auth.access_token"),
        NearMissEvalCase("rowIdentifier", "data.partition_key"),
        NearMissEvalCase("partitionKeyId", "data.partition_key"),
        NearMissEvalCase("customerCap", "billing.credit_limit"),
    )

    for case in cases:
        assert _top_target(lexicon, case.surface) == case.expected_target_id


def test_near_miss_eval_filters_hard_negatives() -> None:
    lexicon = _eval_lexicon()
    cases = (
        NearMissEvalCase("loggerFactory", None),
        NearMissEvalCase("httpClient", None),
        NearMissEvalCase("retryPolicy", None),
        NearMissEvalCase("configLoader", None),
        NearMissEvalCase("userProfile", None),
    )

    for case in cases:
        assert _top_target(lexicon, case.surface) is case.expected_target_id


def test_near_miss_dampens_weak_single_fragment_bridges() -> None:
    lexicon = Lexicon(
        terms=(
            Term(id="auth.access_token", canonical="access token"),
            Term(id="data.partition_key", canonical="partition key"),
        )
    )

    session_report = suggest_near_misses(lexicon, "sessionKey")
    access_level_report = suggest_near_misses(lexicon, "accessLevel")
    access_typo_report = suggest_near_misses(lexicon, "accessTok")
    auth_token_report = suggest_near_misses(lexicon, "authToken")

    assert session_report.suggestions == ()
    assert access_level_report.suggestions == ()
    assert access_typo_report.suggestions[0].target_term_id == "auth.access_token"
    assert auth_token_report.suggestions[0].target_term_id == "auth.access_token"
    assert auth_token_report.suggestions[0].metadata["precision_adjustments"] == ()


def test_near_miss_metadata_records_precision_dampening_below_threshold() -> None:
    lexicon = Lexicon(terms=(Term(id="data.partition_key", canonical="partition key"),))

    report = suggest_near_misses(lexicon, "sessionKey", min_confidence=0.0)

    assert report.suggestions[0].target_term_id == "data.partition_key"
    assert report.suggestions[0].confidence < 0.42
    assert report.suggestions[0].metadata["precision_adjustments"] == (
        {
            "kind": "weak_single_fragment_bridge",
            "fragment": "key",
            "penalty": 0.14,
        },
    )
