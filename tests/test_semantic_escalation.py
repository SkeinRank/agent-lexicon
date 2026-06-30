from __future__ import annotations

import json

from agent_lexicon import (
    Lexicon,
    NoopSemanticNearMissBackend,
    SemanticEscalationReason,
    SemanticNearMissCandidate,
    SemanticNearMissRequest,
    SemanticSuggestionSource,
    Term,
    resolve_text,
    semantic_escalation_hint,
    suggest_near_misses,
)
from agent_lexicon.cli import main


def test_semantic_escalation_hint_marks_gray_zone_confidence() -> None:
    hint = semantic_escalation_hint(confidence=0.51, shared_fragments=("access",))

    assert hint.recommended is True
    assert SemanticEscalationReason.GRAY_ZONE_CONFIDENCE in hint.reasons
    assert SemanticEscalationReason.SINGLE_FRAGMENT_BRIDGE in hint.reasons
    assert hint.to_dict()["backend_name"] == "none"


def test_noop_semantic_backend_returns_unapplied_result() -> None:
    request = SemanticNearMissRequest(
        surface="accessLevel",
        normalized_surface="access level",
        query_fragments=("access", "level"),
        candidates=(
            SemanticNearMissCandidate(
                target_term_id="auth.access_token",
                target_canonical="access token",
                matched_surface="access token",
                confidence=0.51,
            ),
        ),
    )

    result = NoopSemanticNearMissBackend().rerank(request)

    assert result.source == SemanticSuggestionSource.NONE
    assert result.applied is False
    assert result.deterministic is True
    assert result.candidates == request.candidates
    assert result.metadata["reason"] == "no_semantic_backend_configured"


def test_near_miss_metadata_marks_heuristic_source_and_semantic_gate() -> None:
    lexicon = Lexicon(terms=(Term(id="auth.access_token", canonical="access token"),))

    report = suggest_near_misses(lexicon, "authToken")

    assert report.suggestion_count == 1
    assert report.metadata["semantic_escalation_recommended_count"] == 1
    assert report.metadata["semantic_backend"]["applied"] is False
    suggestion = report.suggestions[0]
    assert suggestion.metadata["suggestion_source"] == "heuristic"
    semantic = suggestion.metadata["semantic_escalation"]
    assert semantic["recommended"] is True
    assert "related_fragment_bridge" in semantic["reasons"]


def test_resolver_near_miss_metadata_serializes_semantic_escalation() -> None:
    lexicon = Lexicon(terms=(Term(id="auth.access_token", canonical="access token"),))

    decision = resolve_text(lexicon, "rotate authToken")
    payload = json.loads(json.dumps(decision.metadata))

    item = payload["near_miss_suggestions"][0]["suggestions"][0]
    assert item["metadata"]["suggestion_source"] == "heuristic"
    assert item["metadata"]["semantic_escalation"]["recommended"] is True


def test_cli_resolve_prints_semantic_escalation_hint(tmp_path, capsys) -> None:
    path = tmp_path / "lexicon.yaml"
    path.write_text(
        """
        version: 1
        terms:
          - id: auth.access_token
            canonical: access token
        """,
        encoding="utf-8",
    )

    exit_code = main(["resolve", str(path), "rotate authToken"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Near-miss suggestions:" in captured.out
    assert "semantic_escalation=" in captured.out
