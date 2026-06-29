from __future__ import annotations

from pathlib import Path

import pytest

from agent_lexicon import (
    EvalDatasetError,
    EvalQuery,
    EvalToolCallExpectation,
    ResolutionAction,
    ResolutionStatus,
    ToolGuardAction,
    ToolGuardStatus,
    load_eval_queries,
    loads_eval_queries,
)


EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples" / "customer_limits"


def test_load_eval_queries_from_example_jsonl() -> None:
    queries = load_eval_queries(EXAMPLES_DIR / "queries.jsonl")

    assert len(queries) == 5
    assert queries[0].id == "ambiguous.limit"
    assert queries[0].expected_status == ResolutionStatus.AMBIGUOUS
    assert queries[0].expected_action == ResolutionAction.ASK_CLARIFICATION
    assert queries[0].expected_term_ids == ("billing.credit_limit", "api.rate_limit")
    assert queries[0].tool_calls[0].tool_name == "api.update_rate_limit"
    assert queries[0].tool_calls[0].expected_status == ToolGuardStatus.NEEDS_CLARIFICATION
    assert queries[0].tool_calls[0].expected_action == ToolGuardAction.ASK_CLARIFICATION
    assert queries[0].tool_calls[0].expected_allowed is False


def test_eval_query_to_dict_is_json_serializable() -> None:
    query = EvalQuery(
        id="billing.customer_cap",
        text="increase the customer cap",
        scopes=("billing",),
        expected_status=ResolutionStatus.RESOLVED,
        expected_action=ResolutionAction.USE_TERMS,
        expected_term_ids=("billing.credit_limit",),
        expected_primary_term_id="billing.credit_limit",
        tool_calls=(
            EvalToolCallExpectation(
                tool_name="billing.update_credit_limit",
                expected_status=ToolGuardStatus.ALLOWED,
                expected_action=ToolGuardAction.PROCEED,
                expected_allowed=True,
            ),
        ),
    )

    payload = query.to_dict()

    assert payload["expected_status"] == "resolved"
    assert payload["expected_action"] == "use_terms"
    assert payload["tool_calls"][0]["expected_status"] == "allowed"


def test_load_eval_queries_rejects_duplicate_ids() -> None:
    text = '\n'.join([
        '{"id":"dup","text":"first"}',
        '{"id":"dup","text":"second"}',
    ])

    with pytest.raises(EvalDatasetError, match="duplicate eval query id"):
        loads_eval_queries(text)


def test_load_eval_queries_rejects_invalid_json() -> None:
    with pytest.raises(EvalDatasetError, match="invalid JSON"):
        loads_eval_queries('{"id":')


def test_load_eval_queries_rejects_invalid_resolution_status() -> None:
    with pytest.raises(EvalDatasetError, match="expected_status"):
        loads_eval_queries('{"id":"bad","text":"query","expected_status":"maybe"}')


def test_load_eval_queries_rejects_invalid_tool_status() -> None:
    with pytest.raises(EvalDatasetError, match="tool call expected_status"):
        loads_eval_queries('{"id":"bad","text":"query","tool_calls":[{"tool_name":"x","expected_status":"maybe"}]}')


def test_load_eval_queries_rejects_missing_required_fields() -> None:
    with pytest.raises(EvalDatasetError, match="missing required field"):
        loads_eval_queries('{"id":"bad"}')


def test_load_eval_queries_rejects_primary_term_outside_expected_terms() -> None:
    with pytest.raises(EvalDatasetError, match="expected_primary_term_id"):
        loads_eval_queries(
            '{"id":"bad","text":"query","expected_term_ids":["a"],"expected_primary_term_id":"b"}'
        )
