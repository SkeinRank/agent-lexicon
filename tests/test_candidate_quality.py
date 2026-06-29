from __future__ import annotations

from pathlib import Path

from agent_lexicon import (
    CandidatePriority,
    candidate_cluster_key,
    compute_candidate_quality,
    discover_scout_candidates,
    ingest_local_paths,
    surface_fragments,
)
from agent_lexicon.cli import main


def test_oov_proxy_and_cluster_key_for_code_style_surface() -> None:
    signals = compute_candidate_quality(
        surface="PaymentCoreV2",
        normalized_surface="paymentcorev2",
        kind="identifier",
        score=0.72,
        jargon_score=0.88,
        background_penalty=0.02,
        occurrence_count=3,
        document_count=2,
        negative_count=1,
        cluster_size=2,
    )

    assert surface_fragments("PaymentCoreV2") == ("payment", "core", "v2")
    assert candidate_cluster_key("payments_core") == "payment core"
    assert candidate_cluster_key("PaymentCore") == "payment core"
    assert signals.oov_proxy_score >= 0.4
    assert signals.priority == CandidatePriority.IMPORTANT
    assert "code_style_surface" in signals.priority_reasons


def test_candidate_discovery_attaches_quality_and_clusters(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "billing.md").write_text(
        "PaymentCore owns customer cap checks.\n"
        "payments_core emits CustomerCapReview events.\n"
        "CustomerCapReview calls billing.update_credit_limit.\n",
        encoding="utf-8",
    )
    ingest = ingest_local_paths([docs], root=tmp_path)

    report = discover_scout_candidates(ingest.documents, min_score=0.2, max_candidates=10)

    assert report.cluster_count >= 1
    assert report.important_count >= 1
    candidate = next(item for item in report.candidates if item.surface == "PaymentCore")
    quality = candidate.metadata["quality"]
    cluster = candidate.metadata["cluster"]
    assert quality["cluster_key"] == "payment core"
    assert quality["priority"] in {"important", "later"}
    assert quality["oov_proxy_score"] > 0
    assert cluster["candidate_count"] >= 1


def test_simple_analyze_priority_filter_reports_important_items(tmp_path: Path, capsys) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "billing.md").write_text(
        "PaymentCore owns customer cap checks.\n"
        "CustomerCapReview calls billing.update_credit_limit.\n",
        encoding="utf-8",
    )

    assert main(["init", "--root", str(tmp_path)]) == 0
    capsys.readouterr()
    assert main(["scan", "docs", "--root", str(tmp_path), "--min-score", "0.2", "--max-candidates", "5"]) == 0
    scan_output = capsys.readouterr().out
    assert "important" in scan_output

    assert main(["analyze", "--root", str(tmp_path), "--priority", "important"]) == 0
    analyze_output = capsys.readouterr().out
    assert "Agent Lexicon analyze:" in analyze_output
    assert "oov=" in analyze_output
    assert "cluster=" in analyze_output
