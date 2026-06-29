"""CI and pull-request validation for dictionary-as-code projects."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from agent_lexicon.core import AgentLexiconLoadError, load_lexicon
from agent_lexicon.evals import EvalDatasetError, load_eval_queries, run_behavior_eval

from .diff import SemanticDiffError, SemanticDiffReport, diff_lexicon_files
from .layout import DEFAULT_DICTIONARY_DIR, DictionaryLayoutError, DictionaryLayoutSummary, validate_dictionary_layout
from .merge import SemanticMergeError, SemanticMergeReport, merge_lexicon_files


class DictionaryCheckStatus(str, Enum):
    """Status for one dictionary PR validation check."""

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


class DictionaryCheckKind(str, Enum):
    """Built-in dictionary PR validation check kinds."""

    LAYOUT = "layout"
    BEHAVIOR = "behavior"
    SEMANTIC_DIFF = "semantic_diff"
    SEMANTIC_MERGE = "semantic_merge"


class DictionaryCheckError(ValueError):
    """Raised when dictionary CI validation input is invalid."""


@dataclass(frozen=True, slots=True)
class DictionaryCheckItem:
    """One dictionary CI validation result."""

    kind: DictionaryCheckKind
    status: DictionaryCheckStatus
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        """Return whether this check did not fail."""
        return self.status is not DictionaryCheckStatus.FAILED

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable check payload."""
        return {
            "kind": self.kind.value,
            "status": self.status.value,
            "passed": self.passed,
            "message": self.message,
            "details": _json_ready(dict(self.details)),
        }

    def to_text(self) -> str:
        """Return one compact status line."""
        return f"[{self.status.value.upper()}] {self.kind.value}: {self.message}"


@dataclass(frozen=True, slots=True)
class DictionaryPrCheckReport:
    """Result of local dictionary-as-code CI validation."""

    root_path: str
    layout_dir: str
    checks: tuple[DictionaryCheckItem, ...]

    @property
    def passed(self) -> bool:
        """Return whether all checks passed or were skipped."""
        return all(item.passed for item in self.checks)

    @property
    def failed_count(self) -> int:
        """Return the number of failed checks."""
        return sum(1 for item in self.checks if item.status is DictionaryCheckStatus.FAILED)

    @property
    def passed_count(self) -> int:
        """Return the number of passed checks."""
        return sum(1 for item in self.checks if item.status is DictionaryCheckStatus.PASSED)

    @property
    def skipped_count(self) -> int:
        """Return the number of skipped checks."""
        return sum(1 for item in self.checks if item.status is DictionaryCheckStatus.SKIPPED)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable report payload."""
        return {
            "root_path": self.root_path,
            "layout_dir": self.layout_dir,
            "passed": self.passed,
            "summary": {
                "total": len(self.checks),
                "passed": self.passed_count,
                "failed": self.failed_count,
                "skipped": self.skipped_count,
            },
            "checks": [item.to_dict() for item in self.checks],
        }

    def to_json(self) -> str:
        """Return the report as stable formatted JSON."""
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)


def run_dictionary_pr_checks(
    root: str | Path = ".",
    *,
    layout_dir: str = DEFAULT_DICTIONARY_DIR,
    base_lexicon_path: str | Path | None = None,
    merge_base_path: str | Path | None = None,
    merge_ours_path: str | Path | None = None,
    merge_theirs_path: str | Path | None = None,
    fail_on_semantic_change: bool = False,
    include_deprecated: bool = True,
) -> DictionaryPrCheckReport:
    """Run deterministic dictionary-as-code checks suitable for pull requests.

    The default check validates the local dictionary layout and runs the behavior
    dataset from ``lexicon/queries.jsonl`` against ``lexicon/lexicon.yaml``. If a
    base lexicon is provided, the report also includes a semantic diff. If all
    three merge inputs are provided, it also runs a three-way semantic merge
    check and fails on semantic conflicts.
    """
    root_path = Path(root).expanduser().resolve()
    checks: list[DictionaryCheckItem] = []
    layout_summary: DictionaryLayoutSummary | None = None

    try:
        layout_summary = validate_dictionary_layout(root_path, layout_dir=layout_dir)
    except DictionaryLayoutError as exc:
        checks.append(
            DictionaryCheckItem(
                kind=DictionaryCheckKind.LAYOUT,
                status=DictionaryCheckStatus.FAILED,
                message=str(exc),
                details={"root_path": str(root_path), "layout_dir": layout_dir},
            )
        )
    else:
        checks.append(
            DictionaryCheckItem(
                kind=DictionaryCheckKind.LAYOUT,
                status=DictionaryCheckStatus.PASSED,
                message="dictionary layout is valid",
                details=layout_summary.to_dict(),
            )
        )

    if layout_summary is None:
        checks.append(
            DictionaryCheckItem(
                kind=DictionaryCheckKind.BEHAVIOR,
                status=DictionaryCheckStatus.SKIPPED,
                message="behavior checks skipped because layout validation failed",
            )
        )
    else:
        checks.append(
            _run_behavior_check(
                lexicon_path=Path(layout_summary.layout.lexicon_path),
                queries_path=Path(layout_summary.layout.queries_path),
                include_deprecated=include_deprecated,
            )
        )

    if base_lexicon_path is not None:
        if layout_summary is None:
            checks.append(
                DictionaryCheckItem(
                    kind=DictionaryCheckKind.SEMANTIC_DIFF,
                    status=DictionaryCheckStatus.SKIPPED,
                    message="semantic diff skipped because layout validation failed",
                )
            )
        else:
            checks.append(
                _run_semantic_diff_check(
                    base_lexicon_path=Path(base_lexicon_path),
                    head_lexicon_path=Path(layout_summary.layout.lexicon_path),
                    fail_on_semantic_change=fail_on_semantic_change,
                )
            )

    merge_paths = (merge_base_path, merge_ours_path, merge_theirs_path)
    if any(path is not None for path in merge_paths):
        if not all(path is not None for path in merge_paths):
            checks.append(
                DictionaryCheckItem(
                    kind=DictionaryCheckKind.SEMANTIC_MERGE,
                    status=DictionaryCheckStatus.FAILED,
                    message="semantic merge requires --merge-base, --merge-ours, and --merge-theirs together",
                )
            )
        else:
            checks.append(
                _run_semantic_merge_check(
                    merge_base_path=Path(str(merge_base_path)),
                    merge_ours_path=Path(str(merge_ours_path)),
                    merge_theirs_path=Path(str(merge_theirs_path)),
                )
            )

    return DictionaryPrCheckReport(root_path=str(root_path), layout_dir=layout_dir, checks=tuple(checks))


def _run_behavior_check(*, lexicon_path: Path, queries_path: Path, include_deprecated: bool) -> DictionaryCheckItem:
    try:
        lexicon = load_lexicon(lexicon_path)
        queries = load_eval_queries(queries_path)
        report = run_behavior_eval(lexicon, queries, include_deprecated=include_deprecated)
    except (AgentLexiconLoadError, EvalDatasetError) as exc:
        return DictionaryCheckItem(
            kind=DictionaryCheckKind.BEHAVIOR,
            status=DictionaryCheckStatus.FAILED,
            message=str(exc),
            details={"lexicon_path": str(lexicon_path), "queries_path": str(queries_path)},
        )

    metrics = report.metrics
    status = DictionaryCheckStatus.PASSED if report.passed else DictionaryCheckStatus.FAILED
    message = f"{metrics.passed_checks}/{metrics.total_checks} behavior checks passed across {metrics.query_count} queries"
    return DictionaryCheckItem(
        kind=DictionaryCheckKind.BEHAVIOR,
        status=status,
        message=message,
        details=report.to_dict(),
    )


def _run_semantic_diff_check(
    *,
    base_lexicon_path: Path,
    head_lexicon_path: Path,
    fail_on_semantic_change: bool,
) -> DictionaryCheckItem:
    try:
        report = diff_lexicon_files(base_lexicon_path, head_lexicon_path)
    except SemanticDiffError as exc:
        return DictionaryCheckItem(
            kind=DictionaryCheckKind.SEMANTIC_DIFF,
            status=DictionaryCheckStatus.FAILED,
            message=str(exc),
            details={"base_lexicon_path": str(base_lexicon_path), "head_lexicon_path": str(head_lexicon_path)},
        )

    summary = report.summary
    if fail_on_semantic_change and report.has_changes:
        status = DictionaryCheckStatus.FAILED
        message = f"semantic changes detected: {summary.total} changes"
    elif report.has_changes:
        status = DictionaryCheckStatus.PASSED
        message = f"semantic changes detected for review: {summary.total} changes"
    else:
        status = DictionaryCheckStatus.PASSED
        message = "no semantic changes detected"
    return DictionaryCheckItem(
        kind=DictionaryCheckKind.SEMANTIC_DIFF,
        status=status,
        message=message,
        details=report.to_dict(),
    )


def _run_semantic_merge_check(
    *,
    merge_base_path: Path,
    merge_ours_path: Path,
    merge_theirs_path: Path,
) -> DictionaryCheckItem:
    try:
        report = merge_lexicon_files(merge_base_path, merge_ours_path, merge_theirs_path)
    except SemanticMergeError as exc:
        return DictionaryCheckItem(
            kind=DictionaryCheckKind.SEMANTIC_MERGE,
            status=DictionaryCheckStatus.FAILED,
            message=str(exc),
            details={
                "merge_base_path": str(merge_base_path),
                "merge_ours_path": str(merge_ours_path),
                "merge_theirs_path": str(merge_theirs_path),
            },
        )

    status = DictionaryCheckStatus.FAILED if report.has_conflicts else DictionaryCheckStatus.PASSED
    message = (
        f"semantic merge has {report.conflict_count} conflicts"
        if report.has_conflicts
        else "semantic merge is clean"
    )
    return DictionaryCheckItem(
        kind=DictionaryCheckKind.SEMANTIC_MERGE,
        status=status,
        message=message,
        details=report.to_dict(),
    )


def _json_ready(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_ready(inner) for key, inner in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value
