from __future__ import annotations

from pathlib import Path

from agent_lexicon import (
    ProxyOovScorer,
    TokenizerOovScorer,
    compute_candidate_quality,
    discover_scout_candidates,
    ingest_local_paths,
)
from agent_lexicon.cli import main


class _FakeEncoding:
    def __init__(self, tokens: list[str]) -> None:
        self.tokens = tokens


class _FakeTokenizer:
    def encode(self, surface: str) -> _FakeEncoding:
        if surface == "PaymentCoreV2":
            return _FakeEncoding(["payment", "##core", "##v", "##2"])
        return _FakeEncoding([surface.casefold()])


def test_proxy_oov_scorer_is_dependency_free() -> None:
    result = ProxyOovScorer().score("PaymentCoreV2")

    assert result.source == "proxy"
    assert result.score > 0
    assert result.fragment_count == 3
    assert result.to_dict()["metadata"]["camel_boundary_count"] >= 1


def test_tokenizer_oov_scorer_uses_token_fragmentation() -> None:
    scorer = TokenizerOovScorer("fake-tokenizer", tokenizer=_FakeTokenizer())
    result = scorer.score("PaymentCoreV2")

    assert result.source == "tokenizer"
    assert result.tokenizer_name == "fake-tokenizer"
    assert result.token_count == 4
    assert result.continuation_token_count == 3
    assert result.score > 0


def test_candidate_quality_can_use_tokenizer_oov_result() -> None:
    scorer = TokenizerOovScorer("fake-tokenizer", tokenizer=_FakeTokenizer())
    oov_result = scorer.score("PaymentCoreV2")

    signals = compute_candidate_quality(
        surface="PaymentCoreV2",
        normalized_surface="paymentcorev2",
        kind="identifier",
        score=0.7,
        jargon_score=0.9,
        background_penalty=0.02,
        occurrence_count=3,
        document_count=2,
        oov_result=oov_result,
    )

    assert signals.oov_source == "tokenizer"
    assert signals.oov_tokenizer_score == oov_result.score
    assert signals.oov_score >= signals.oov_proxy_score
    assert "tokenizer_oov_signal" in signals.priority_reasons
    assert signals.to_dict()["metadata"]["oov"]["tokenizer_name"] == "fake-tokenizer"


def test_discovery_attaches_tokenizer_oov_metadata(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "billing.md").write_text(
        "PaymentCoreV2 owns CustomerCapReview decisions.\n"
        "PaymentCoreV2 emits billing.update_credit_limit events.\n",
        encoding="utf-8",
    )
    ingest = ingest_local_paths([docs], root=tmp_path)
    scorer = TokenizerOovScorer("fake-tokenizer", tokenizer=_FakeTokenizer())

    report = discover_scout_candidates(ingest.documents, min_score=0.2, max_candidates=5, oov_scorer=scorer)
    candidate = next(item for item in report.candidates if item.surface == "PaymentCoreV2")
    quality = candidate.metadata["quality"]

    assert quality["oov_source"] == "tokenizer"
    assert quality["oov_tokenizer_score"] is not None
    assert quality["metadata"]["oov"]["tokenizer_name"] == "fake-tokenizer"


def test_cli_scan_accepts_proxy_oov_tokenizer(tmp_path: Path, capsys) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "billing.md").write_text("PaymentCoreV2 owns customer cap checks.\n", encoding="utf-8")

    assert main(["init", "--root", str(tmp_path)]) == 0
    capsys.readouterr()
    assert main([
        "scan",
        "docs",
        "--root",
        str(tmp_path),
        "--min-score",
        "0.2",
        "--oov-tokenizer",
        "proxy",
        "--json",
    ]) == 0
    output = capsys.readouterr().out
    assert '"oov_source": "proxy"' in output
