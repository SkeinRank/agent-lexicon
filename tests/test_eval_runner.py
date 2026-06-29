from __future__ import annotations

from pathlib import Path

from agent_lexicon import (
    EvalQuery,
    ResolutionAction,
    ResolutionStatus,
    ToolGuardAction,
    ToolGuardStatus,
    load_eval_queries,
    load_lexicon,
    run_behavior_eval,
)


EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples" / "customer_limits"


def test_run_behavior_eval_reports_expected_metrics() -> None:
    lexicon = load_lexicon(EXAMPLES_DIR / "lexicon.yaml")
    queries = load_eval_queries(EXAMPLES_DIR / "queries.jsonl")

    report = run_behavior_eval(lexicon, queries)

    assert report.passed is True
    assert report.metrics.query_count == 5
    assert report.metrics.total_checks == report.metrics.passed_checks
    assert report.metrics.overall_accuracy == 1.0
    assert report.metrics.ambiguity_detection_rate == 1.0
    assert report.metrics.canonicalization_accuracy == 1.0
    assert report.metrics.wrong_tool_prevention_rate == 1.0
    assert report.metrics.tool_allowed_accuracy == 1.0


def test_run_behavior_eval_marks_wrong_expectation_as_failed() -> None:
    lexicon = load_lexicon(EXAMPLES_DIR / "lexicon.yaml")
    queries = (
        EvalQuery(
            id="wrong.expectation",
            text="increase the limit",
            expected_status=ResolutionStatus.RESOLVED,
            expected_action=ResolutionAction.USE_TERMS,
            expected_term_ids=("billing.credit_limit",),
        ),
    )

    report = run_behavior_eval(lexicon, queries)

    assert report.passed is False
    assert report.metrics.resolution_status_accuracy == 0.0
    assert report.metrics.resolution_action_accuracy == 0.0
    assert report.metrics.canonicalization_accuracy == 0.0
    assert report.results[0].passed is False


def test_eval_report_to_dict_is_json_serializable() -> None:
    lexicon = load_lexicon(EXAMPLES_DIR / "lexicon.yaml")
    queries = load_eval_queries(EXAMPLES_DIR / "queries.jsonl")

    payload = run_behavior_eval(lexicon, queries).to_dict()

    assert payload["passed"] is True
    assert payload["metrics"]["wrong_tool_prevention_rate"] == 1.0
    assert payload["results"][0]["resolution"]["status"] == "ambiguous"


def test_metrics_support_not_applicable_categories() -> None:
    lexicon = load_lexicon(EXAMPLES_DIR / "lexicon.yaml")
    report = run_behavior_eval(
        lexicon,
        (
            EvalQuery(
                id="minimal",
                text="increase the customer cap",
                expected_status=ResolutionStatus.RESOLVED,
            ),
        ),
    )

    assert report.metrics.overall_accuracy == 1.0
    assert report.metrics.canonicalization_accuracy is None
    assert report.metrics.wrong_tool_prevention_rate is None
    assert report.metrics.tool_allowed_accuracy is None
