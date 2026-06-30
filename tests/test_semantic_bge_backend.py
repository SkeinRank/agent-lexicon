from __future__ import annotations

from agent_lexicon import (
    BgeSemanticNearMissBackend,
    DEFAULT_BGE_BASE_MODEL,
    Lexicon,
    SemanticSuggestionSource,
    Term,
    resolve_text,
    suggest_near_misses,
)
from agent_lexicon.cli import main


class FakeEmbeddingModel:
    def encode(self, texts, **kwargs):
        vectors = []
        for text in texts:
            lowered = text.lower()
            if "access token" in lowered or "auth token" in lowered:
                vectors.append([1.0, 0.0, 0.0])
            elif "session token" in lowered:
                vectors.append([0.0, 1.0, 0.0])
            else:
                vectors.append([0.0, 0.0, 1.0])
        return vectors


def test_bge_backend_filters_and_reranks_escalated_candidates() -> None:
    lexicon = Lexicon(
        terms=(
            Term(id="auth.access_token", canonical="access token"),
            Term(id="auth.session_token", canonical="session token"),
        )
    )
    backend = BgeSemanticNearMissBackend(model=FakeEmbeddingModel(), min_semantic_score=0.5)

    report = suggest_near_misses(lexicon, "authToken", max_suggestions=3, semantic_backend=backend)

    assert report.metadata["semantic_backend"]["source"] == SemanticSuggestionSource.SEMANTIC.value
    assert report.metadata["semantic_backend"]["applied"] is True
    assert [suggestion.target_term_id for suggestion in report.suggestions] == ["auth.access_token"]
    suggestion = report.suggestions[0]
    assert suggestion.metadata["suggestion_source"] == "semantic"
    assert suggestion.metadata["semantic_applied"] is True
    assert suggestion.metadata["semantic_backend"] == "bge-base"
    assert suggestion.metadata["semantic_score"] == 1.0


def test_bge_backend_is_available_without_loading_model_until_used() -> None:
    backend = BgeSemanticNearMissBackend(model_name=DEFAULT_BGE_BASE_MODEL)

    assert backend.model_name == DEFAULT_BGE_BASE_MODEL
    assert backend.deterministic is False


def test_resolve_text_accepts_optional_semantic_backend() -> None:
    lexicon = Lexicon(
        terms=(
            Term(id="auth.access_token", canonical="access token"),
            Term(id="auth.session_token", canonical="session token"),
        )
    )
    backend = BgeSemanticNearMissBackend(model=FakeEmbeddingModel(), min_semantic_score=0.5)

    decision = resolve_text(lexicon, "rotate authToken", near_miss_semantic_backend=backend)

    item = decision.metadata["near_miss_suggestions"][0]["suggestions"][0]
    assert item["target_term_id"] == "auth.access_token"
    assert item["metadata"]["suggestion_source"] == "semantic"
    assert item["metadata"]["semantic_score"] == 1.0


def test_cli_resolve_can_use_semantic_backend_when_enabled(tmp_path, capsys, monkeypatch) -> None:
    path = tmp_path / "lexicon.yaml"
    path.write_text(
        """
        version: 1
        terms:
          - id: auth.access_token
            canonical: access token
          - id: auth.session_token
            canonical: session token
        """,
        encoding="utf-8",
    )

    def fake_backend_from_cli(*, enabled, model_name, min_semantic_score):
        assert enabled is True
        assert model_name == DEFAULT_BGE_BASE_MODEL
        return BgeSemanticNearMissBackend(model=FakeEmbeddingModel(), min_semantic_score=0.5)

    monkeypatch.setattr("agent_lexicon.cli._semantic_backend_from_cli", fake_backend_from_cli)

    exit_code = main(["resolve", str(path), "rotate authToken", "--semantic-near-miss"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "auth.access_token" in captured.out
    assert "semantic_score=1.000" in captured.out
    assert "semantic_backend=bge-base" in captured.out
