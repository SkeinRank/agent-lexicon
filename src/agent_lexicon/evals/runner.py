"""Behavior evaluation for Agent Lexicon runtime decisions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from agent_lexicon.core import (
    Lexicon,
    ResolutionDecision,
    ToolGuardDecision,
    guard_tool_call,
    resolve_text,
)

from .dataset import EvalQuery, EvalToolCallExpectation


def _ratio(passed: int, total: int) -> float | None:
    if total == 0:
        return None
    return passed / total


@dataclass(frozen=True, slots=True)
class EvalToolCallResult:
    """Observed result for one tool-call expectation."""

    expectation: EvalToolCallExpectation
    decision: ToolGuardDecision
    status_ok: bool | None = None
    action_ok: bool | None = None
    allowed_ok: bool | None = None

    @property
    def checks_total(self) -> int:
        """Return the number of explicit expectations checked."""
        return sum(value is not None for value in (self.status_ok, self.action_ok, self.allowed_ok))

    @property
    def checks_passed(self) -> int:
        """Return the number of explicit expectations that passed."""
        return sum(value is True for value in (self.status_ok, self.action_ok, self.allowed_ok))

    @property
    def passed(self) -> bool:
        """Return whether all explicit expectations passed."""
        return self.checks_passed == self.checks_total

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "tool_name": self.expectation.tool_name,
            "status_ok": self.status_ok,
            "action_ok": self.action_ok,
            "allowed_ok": self.allowed_ok,
            "passed": self.passed,
            "decision": self.decision.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class EvalQueryResult:
    """Observed result for one eval query."""

    query: EvalQuery
    resolution: ResolutionDecision
    status_ok: bool | None = None
    action_ok: bool | None = None
    term_ids_ok: bool | None = None
    primary_term_ok: bool | None = None
    tool_calls: tuple[EvalToolCallResult, ...] = ()

    @property
    def checks_total(self) -> int:
        """Return the number of explicit expectations checked."""
        resolution_checks = sum(
            value is not None
            for value in (self.status_ok, self.action_ok, self.term_ids_ok, self.primary_term_ok)
        )
        return resolution_checks + sum(tool_result.checks_total for tool_result in self.tool_calls)

    @property
    def checks_passed(self) -> int:
        """Return the number of explicit expectations that passed."""
        resolution_passed = sum(
            value is True
            for value in (self.status_ok, self.action_ok, self.term_ids_ok, self.primary_term_ok)
        )
        return resolution_passed + sum(tool_result.checks_passed for tool_result in self.tool_calls)

    @property
    def passed(self) -> bool:
        """Return whether all explicit expectations passed."""
        return self.checks_passed == self.checks_total

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "id": self.query.id,
            "text": self.query.text,
            "status_ok": self.status_ok,
            "action_ok": self.action_ok,
            "term_ids_ok": self.term_ids_ok,
            "primary_term_ok": self.primary_term_ok,
            "passed": self.passed,
            "resolution": self.resolution.to_dict(),
            "tool_calls": [tool_call.to_dict() for tool_call in self.tool_calls],
        }


@dataclass(frozen=True, slots=True)
class BehaviorMetrics:
    """Aggregated behavior metrics for an eval run."""

    query_count: int
    total_checks: int
    passed_checks: int
    resolution_status_total: int = 0
    resolution_status_passed: int = 0
    resolution_action_total: int = 0
    resolution_action_passed: int = 0
    canonicalization_total: int = 0
    canonicalization_passed: int = 0
    ambiguity_total: int = 0
    ambiguity_passed: int = 0
    tool_status_total: int = 0
    tool_status_passed: int = 0
    tool_action_total: int = 0
    tool_action_passed: int = 0
    tool_allowed_total: int = 0
    tool_allowed_passed: int = 0
    wrong_tool_prevention_total: int = 0
    wrong_tool_prevention_passed: int = 0

    @property
    def overall_accuracy(self) -> float | None:
        """Return all explicit checks passed divided by total checks."""
        return _ratio(self.passed_checks, self.total_checks)

    @property
    def resolution_status_accuracy(self) -> float | None:
        """Return resolution status accuracy."""
        return _ratio(self.resolution_status_passed, self.resolution_status_total)

    @property
    def resolution_action_accuracy(self) -> float | None:
        """Return resolution action accuracy."""
        return _ratio(self.resolution_action_passed, self.resolution_action_total)

    @property
    def canonicalization_accuracy(self) -> float | None:
        """Return expected canonical term-id accuracy."""
        return _ratio(self.canonicalization_passed, self.canonicalization_total)

    @property
    def ambiguity_detection_rate(self) -> float | None:
        """Return how often expected ambiguous queries were detected as ambiguous."""
        return _ratio(self.ambiguity_passed, self.ambiguity_total)

    @property
    def tool_status_accuracy(self) -> float | None:
        """Return tool guard status accuracy."""
        return _ratio(self.tool_status_passed, self.tool_status_total)

    @property
    def tool_action_accuracy(self) -> float | None:
        """Return tool guard action accuracy."""
        return _ratio(self.tool_action_passed, self.tool_action_total)

    @property
    def tool_allowed_accuracy(self) -> float | None:
        """Return tool allowed/block expectation accuracy."""
        return _ratio(self.tool_allowed_passed, self.tool_allowed_total)

    @property
    def wrong_tool_prevention_rate(self) -> float | None:
        """Return how often expected unsafe tool calls were prevented."""
        return _ratio(self.wrong_tool_prevention_passed, self.wrong_tool_prevention_total)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "query_count": self.query_count,
            "total_checks": self.total_checks,
            "passed_checks": self.passed_checks,
            "overall_accuracy": self.overall_accuracy,
            "resolution_status_accuracy": self.resolution_status_accuracy,
            "resolution_action_accuracy": self.resolution_action_accuracy,
            "canonicalization_accuracy": self.canonicalization_accuracy,
            "ambiguity_detection_rate": self.ambiguity_detection_rate,
            "tool_status_accuracy": self.tool_status_accuracy,
            "tool_action_accuracy": self.tool_action_accuracy,
            "tool_allowed_accuracy": self.tool_allowed_accuracy,
            "wrong_tool_prevention_rate": self.wrong_tool_prevention_rate,
            "counts": {
                "resolution_status": [self.resolution_status_passed, self.resolution_status_total],
                "resolution_action": [self.resolution_action_passed, self.resolution_action_total],
                "canonicalization": [self.canonicalization_passed, self.canonicalization_total],
                "ambiguity": [self.ambiguity_passed, self.ambiguity_total],
                "tool_status": [self.tool_status_passed, self.tool_status_total],
                "tool_action": [self.tool_action_passed, self.tool_action_total],
                "tool_allowed": [self.tool_allowed_passed, self.tool_allowed_total],
                "wrong_tool_prevention": [
                    self.wrong_tool_prevention_passed,
                    self.wrong_tool_prevention_total,
                ],
            },
        }


@dataclass(frozen=True, slots=True)
class EvalReport:
    """Complete eval report with per-query results and aggregate metrics."""

    results: tuple[EvalQueryResult, ...]
    metrics: BehaviorMetrics
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        """Return whether all explicit checks passed."""
        return self.metrics.total_checks == self.metrics.passed_checks

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "passed": self.passed,
            "metrics": self.metrics.to_dict(),
            "results": [result.to_dict() for result in self.results],
            "metadata": dict(self.metadata),
        }


def run_behavior_eval(
    lexicon: Lexicon,
    queries: Iterable[EvalQuery],
    *,
    include_deprecated: bool = True,
) -> EvalReport:
    """Run a deterministic behavior eval against a lexicon and query dataset."""
    query_results = tuple(
        _run_query(lexicon, query, include_deprecated=include_deprecated)
        for query in queries
    )
    metrics = _build_metrics(query_results)
    return EvalReport(results=query_results, metrics=metrics)


def _run_query(lexicon: Lexicon, query: EvalQuery, *, include_deprecated: bool) -> EvalQueryResult:
    resolution = resolve_text(
        lexicon,
        query.text,
        scopes=query.scopes or None,
        include_deprecated=include_deprecated,
    )
    actual_term_ids = tuple(candidate.term_id for candidate in resolution.candidates)

    tool_results = tuple(
        _run_tool_call(lexicon, query, expectation, include_deprecated=include_deprecated)
        for expectation in query.tool_calls
    )
    return EvalQueryResult(
        query=query,
        resolution=resolution,
        status_ok=(resolution.status == query.expected_status) if query.expected_status is not None else None,
        action_ok=(resolution.action == query.expected_action) if query.expected_action is not None else None,
        term_ids_ok=(set(actual_term_ids) == set(query.expected_term_ids)) if query.expected_term_ids else None,
        primary_term_ok=(resolution.primary_term_id == query.expected_primary_term_id)
        if query.expected_primary_term_id is not None
        else None,
        tool_calls=tool_results,
    )


def _run_tool_call(
    lexicon: Lexicon,
    query: EvalQuery,
    expectation: EvalToolCallExpectation,
    *,
    include_deprecated: bool,
) -> EvalToolCallResult:
    decision = guard_tool_call(
        lexicon,
        query.text,
        tool_name=expectation.tool_name,
        scopes=query.scopes or None,
        include_deprecated=include_deprecated,
    )
    return EvalToolCallResult(
        expectation=expectation,
        decision=decision,
        status_ok=(decision.status == expectation.expected_status) if expectation.expected_status is not None else None,
        action_ok=(decision.action == expectation.expected_action) if expectation.expected_action is not None else None,
        allowed_ok=(decision.is_allowed == expectation.expected_allowed)
        if expectation.expected_allowed is not None
        else None,
    )


def _build_metrics(results: tuple[EvalQueryResult, ...]) -> BehaviorMetrics:
    builder = _MetricBuilder()
    for result in results:
        builder.add_query(result)
    return builder.to_metrics(query_count=len(results))


class _MetricBuilder:
    def __init__(self) -> None:
        self.total_checks = 0
        self.passed_checks = 0
        self.resolution_status_total = 0
        self.resolution_status_passed = 0
        self.resolution_action_total = 0
        self.resolution_action_passed = 0
        self.canonicalization_total = 0
        self.canonicalization_passed = 0
        self.ambiguity_total = 0
        self.ambiguity_passed = 0
        self.tool_status_total = 0
        self.tool_status_passed = 0
        self.tool_action_total = 0
        self.tool_action_passed = 0
        self.tool_allowed_total = 0
        self.tool_allowed_passed = 0
        self.wrong_tool_prevention_total = 0
        self.wrong_tool_prevention_passed = 0

    def add_query(self, result: EvalQueryResult) -> None:
        self.total_checks += result.checks_total
        self.passed_checks += result.checks_passed
        if result.status_ok is not None:
            self.resolution_status_total += 1
            self.resolution_status_passed += int(result.status_ok)
        if result.action_ok is not None:
            self.resolution_action_total += 1
            self.resolution_action_passed += int(result.action_ok)
        if result.term_ids_ok is not None:
            self.canonicalization_total += 1
            self.canonicalization_passed += int(result.term_ids_ok)
        if result.query.expected_status is not None and result.query.expected_status.value == "ambiguous":
            self.ambiguity_total += 1
            self.ambiguity_passed += int(result.resolution.status.value == "ambiguous")
        for tool_result in result.tool_calls:
            self.add_tool_call(tool_result)

    def add_tool_call(self, result: EvalToolCallResult) -> None:
        if result.status_ok is not None:
            self.tool_status_total += 1
            self.tool_status_passed += int(result.status_ok)
        if result.action_ok is not None:
            self.tool_action_total += 1
            self.tool_action_passed += int(result.action_ok)
        if result.allowed_ok is not None:
            self.tool_allowed_total += 1
            self.tool_allowed_passed += int(result.allowed_ok)
        if result.expectation.expected_allowed is False:
            self.wrong_tool_prevention_total += 1
            self.wrong_tool_prevention_passed += int(not result.decision.is_allowed)

    def to_metrics(self, *, query_count: int) -> BehaviorMetrics:
        return BehaviorMetrics(
            query_count=query_count,
            total_checks=self.total_checks,
            passed_checks=self.passed_checks,
            resolution_status_total=self.resolution_status_total,
            resolution_status_passed=self.resolution_status_passed,
            resolution_action_total=self.resolution_action_total,
            resolution_action_passed=self.resolution_action_passed,
            canonicalization_total=self.canonicalization_total,
            canonicalization_passed=self.canonicalization_passed,
            ambiguity_total=self.ambiguity_total,
            ambiguity_passed=self.ambiguity_passed,
            tool_status_total=self.tool_status_total,
            tool_status_passed=self.tool_status_passed,
            tool_action_total=self.tool_action_total,
            tool_action_passed=self.tool_action_passed,
            tool_allowed_total=self.tool_allowed_total,
            tool_allowed_passed=self.tool_allowed_passed,
            wrong_tool_prevention_total=self.wrong_tool_prevention_total,
            wrong_tool_prevention_passed=self.wrong_tool_prevention_passed,
        )


__all__ = [
    "BehaviorMetrics",
    "EvalReport",
    "EvalQueryResult",
    "EvalToolCallResult",
    "run_behavior_eval",
]
