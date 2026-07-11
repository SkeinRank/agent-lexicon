"""Baseline ratchet support for existing terminology debt.

A baseline captures the current count of reviewable terminology findings by
surface, file, and kind. Future checks can suppress the captured debt while
still failing when a file grows new occurrences of the same finding.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
from typing import Any, Iterable, Mapping, Sequence

from agent_lexicon.core.models import Lexicon
from agent_lexicon.core.snapshots import lexicon_snapshot_ref
from agent_lexicon.scout.git_merge import (
    GitDiffAddedLine,
    GitMergeReviewKind,
    GitMergeTerminologyReport,
    build_git_merge_terminology_report,
)
from agent_lexicon.scout.lint_diff import LintFinding
from agent_lexicon.text import normalized_fragment_surface

BASELINE_SCHEMA_VERSION = "baseline-v1"
DEFAULT_BASELINE_PATH = Path("lexicon") / "baseline.json"
_BASELINE_SUFFIXES = (
    ".md",
    ".txt",
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".cs",
    ".sql",
    ".yaml",
    ".yml",
    ".json",
    ".toml",
    ".sh",
)


class BaselineError(RuntimeError):
    """Raised when a terminology baseline cannot be read or written."""


@dataclass(frozen=True, slots=True)
class BaselineIssue:
    """Counted terminology issue used by the baseline ratchet."""

    surface: str
    file: str
    kind: str
    count: int = 1
    normalized_surface: str = ""

    def __post_init__(self) -> None:
        surface = str(self.surface).strip()
        file = str(self.file).strip()
        kind = str(self.kind).strip()
        normalized = str(self.normalized_surface or normalized_fragment_surface(surface)).strip()
        count = int(self.count)
        if not surface:
            raise BaselineError("baseline issue surface must not be empty")
        if not file:
            raise BaselineError("baseline issue file must not be empty")
        if not kind:
            raise BaselineError("baseline issue kind must not be empty")
        if count < 1:
            raise BaselineError("baseline issue count must be greater than 0")
        object.__setattr__(self, "surface", surface)
        object.__setattr__(self, "file", file)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "normalized_surface", normalized or surface.casefold())
        object.__setattr__(self, "count", count)

    @property
    def key(self) -> tuple[str, str, str]:
        """Return the ratchet key: normalized surface, file, and kind."""
        return (self.normalized_surface, self.file, self.kind)

    def to_dict(self) -> dict[str, Any]:
        return {
            "surface": self.surface,
            "normalized_surface": self.normalized_surface,
            "file": self.file,
            "kind": self.kind,
            "count": self.count,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BaselineIssue":
        return cls(
            surface=str(data.get("surface", "")),
            normalized_surface=str(data.get("normalized_surface", "")),
            file=str(data.get("file", "")),
            kind=str(data.get("kind", "")),
            count=int(data.get("count", 0)),
        )


@dataclass(frozen=True, slots=True)
class TerminologyBaseline:
    """Stored baseline document for existing terminology debt."""

    schema_version: str = BASELINE_SCHEMA_VERSION
    lexicon_snapshot: str = ""
    created_at: str = ""
    entries: tuple[BaselineIssue, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.schema_version != BASELINE_SCHEMA_VERSION:
            raise BaselineError(f"unsupported baseline schema_version: {self.schema_version}")
        if not isinstance(self.entries, tuple):
            object.__setattr__(self, "entries", tuple(self.entries))
        for entry in self.entries:
            if not isinstance(entry, BaselineIssue):
                raise BaselineError("baseline entries must be BaselineIssue values")
        object.__setattr__(self, "metadata", dict(self.metadata or {}))

    @property
    def total_count(self) -> int:
        return sum(entry.count for entry in self.entries)

    @property
    def file_count(self) -> int:
        return len({entry.file for entry in self.entries})

    def counts_by_key(self) -> Counter[tuple[str, str, str]]:
        counts: Counter[tuple[str, str, str]] = Counter()
        for entry in self.entries:
            counts[entry.key] += entry.count
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "lexicon_snapshot": self.lexicon_snapshot,
            "created_at": self.created_at,
            "entries": [entry.to_dict() for entry in self.entries],
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TerminologyBaseline":
        raw_entries = data.get("entries", [])
        if not isinstance(raw_entries, list):
            raise BaselineError("baseline entries must be a list")
        return cls(
            schema_version=str(data.get("schema_version", "")),
            lexicon_snapshot=str(data.get("lexicon_snapshot", "")),
            created_at=str(data.get("created_at", "")),
            entries=tuple(BaselineIssue.from_dict(entry) for entry in raw_entries),
            metadata=data.get("metadata", {}) if isinstance(data.get("metadata", {}), Mapping) else {},
        )


@dataclass(frozen=True, slots=True)
class BaselineComparison:
    """Comparison between stored baseline counts and current findings."""

    baseline_total: int
    current_total: int
    suppressed_count: int
    new_count: int
    growth_keys: frozenset[tuple[str, str, str]] = frozenset()

    @property
    def delta(self) -> int:
        return self.current_total - self.baseline_total

    @property
    def has_growth(self) -> bool:
        return self.new_count > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline_total": self.baseline_total,
            "current_total": self.current_total,
            "delta": self.delta,
            "suppressed_count": self.suppressed_count,
            "new_count": self.new_count,
            "has_growth": self.has_growth,
        }

    def to_text(self) -> str:
        delta = self.delta
        sign = "+" if delta > 0 else ""
        return (
            f"baseline: {self.baseline_total} -> {self.current_total} "
            f"({sign}{delta}); new violations: {self.new_count}"
        )


@dataclass(frozen=True, slots=True)
class BaselineFilterResult:
    """Result of filtering a report against a terminology baseline."""

    suppressed_count: int
    new_count: int
    baseline_total: int
    current_total: int
    growth_keys: frozenset[tuple[str, str, str]] = frozenset()

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline_total": self.baseline_total,
            "current_total": self.current_total,
            "suppressed_count": self.suppressed_count,
            "new_count": self.new_count,
            "has_growth": self.new_count > 0,
        }


def default_baseline_path(root: str | Path) -> Path:
    """Return the default baseline path for a project root."""
    return Path(root).resolve() / DEFAULT_BASELINE_PATH


def load_baseline(path: str | Path) -> TerminologyBaseline:
    """Load a baseline JSON document."""
    baseline_path = Path(path)
    try:
        payload = json.loads(baseline_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise BaselineError(f"unable to read baseline: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise BaselineError(f"invalid baseline JSON: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise BaselineError("baseline root must be a JSON object")
    return TerminologyBaseline.from_dict(payload)


def write_baseline(path: str | Path, baseline: TerminologyBaseline) -> None:
    """Write a baseline JSON document."""
    baseline_path = Path(path)
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_path.write_text(
        json.dumps(baseline.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def build_baseline(lexicon: Lexicon, *, root: str | Path, paths: Sequence[str] | None = None) -> TerminologyBaseline:
    """Build a baseline document from the current project tree."""
    root_path = Path(root).resolve()
    issues = collect_repository_issues(lexicon, root=root_path, paths=paths)
    entries = _issues_to_entries(issues)
    return TerminologyBaseline(
        lexicon_snapshot=lexicon_snapshot_ref(lexicon),
        created_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        entries=tuple(entries),
        metadata={
            "root": str(root_path),
            "file_count": len({issue.file for issue in issues}),
        },
    )


def compare_baseline(baseline: TerminologyBaseline, current_issues: Iterable[BaselineIssue]) -> BaselineComparison:
    """Compare stored baseline counts to current issue counts."""
    baseline_counts = baseline.counts_by_key()
    current_counts = _count_issues(current_issues)
    suppressed = 0
    new_count = 0
    growth_keys: set[tuple[str, str, str]] = set()
    for key, current_count in current_counts.items():
        baseline_count = baseline_counts.get(key, 0)
        suppressed += min(current_count, baseline_count)
        if current_count > baseline_count:
            new_count += current_count - baseline_count
            growth_keys.add(key)
    return BaselineComparison(
        baseline_total=baseline.total_count,
        current_total=sum(current_counts.values()),
        suppressed_count=suppressed,
        new_count=new_count,
        growth_keys=frozenset(growth_keys),
    )


def issue_from_lint_finding(finding: LintFinding, *, count: int = 1) -> BaselineIssue:
    """Convert a lint finding to a baseline issue."""
    return BaselineIssue(
        surface=finding.surface,
        file=finding.path,
        kind=_kind_from_lint_level(finding.level),
        count=count,
    )


def issues_from_lint_findings(findings: Iterable[LintFinding]) -> tuple[BaselineIssue, ...]:
    """Convert lint findings to baseline issues."""
    return tuple(issue_from_lint_finding(finding) for finding in findings)


def issues_from_merge_report(report: GitMergeTerminologyReport) -> tuple[BaselineIssue, ...]:
    """Convert a merge report to baseline issues."""
    issues: list[BaselineIssue] = []
    for occurrence in report.known_occurrences:
        if occurrence.deprecated and occurrence.matched_text != occurrence.canonical:
            issues.append(BaselineIssue(surface=occurrence.matched_text, file=occurrence.path, kind="deprecated"))
    for identifier in report.likely_aliases:
        issues.append(BaselineIssue(surface=identifier.surface, file=identifier.path, kind="likely_alias"))
    for identifier in report.likely_new_terms:
        issues.append(BaselineIssue(surface=identifier.surface, file=identifier.path, kind="likely_new_term"))
    for identifier in report.unresolved_unknowns:
        issues.append(BaselineIssue(surface=identifier.surface, file=identifier.path, kind="unresolved_identifier"))
    return tuple(issues)


def filter_lint_findings_with_baseline(
    findings: Sequence[LintFinding],
    *,
    baseline: TerminologyBaseline,
    current_issues: Iterable[BaselineIssue] | None = None,
) -> tuple[tuple[LintFinding, ...], BaselineFilterResult]:
    """Suppress lint findings that are already captured by the baseline."""
    if current_issues is None:
        current_issues = issues_from_lint_findings(findings)
    comparison = compare_baseline(baseline, current_issues)
    visible: list[LintFinding] = []
    suppressed = 0
    for finding in findings:
        issue = issue_from_lint_finding(finding)
        if issue.key in comparison.growth_keys:
            visible.append(finding)
        else:
            suppressed += 1
    return tuple(visible), BaselineFilterResult(
        baseline_total=comparison.baseline_total,
        current_total=comparison.current_total,
        suppressed_count=suppressed,
        new_count=comparison.new_count,
        growth_keys=comparison.growth_keys,
    )


def filter_merge_report_with_baseline(
    report: GitMergeTerminologyReport,
    *,
    baseline: TerminologyBaseline,
    current_issues: Iterable[BaselineIssue] | None = None,
) -> tuple[GitMergeTerminologyReport, BaselineFilterResult]:
    """Suppress merge-report review items that are already captured by the baseline."""
    if current_issues is None:
        current_issues = issues_from_merge_report(report)
    comparison = compare_baseline(baseline, current_issues)
    visible_known = []
    visible_unknown = []
    suppressed = 0
    for occurrence in report.known_occurrences:
        if occurrence.deprecated and occurrence.matched_text != occurrence.canonical:
            issue = BaselineIssue(surface=occurrence.matched_text, file=occurrence.path, kind="deprecated")
            if issue.key not in comparison.growth_keys:
                suppressed += 1
                continue
        visible_known.append(occurrence)
    for identifier in report.unknown_identifiers:
        kind = identifier.review_kind.value
        issue = BaselineIssue(surface=identifier.surface, file=identifier.path, kind=kind)
        if issue.key in comparison.growth_keys:
            visible_unknown.append(identifier)
        else:
            suppressed += 1
    metadata = dict(report.metadata)
    metadata["baseline"] = BaselineFilterResult(
        baseline_total=comparison.baseline_total,
        current_total=comparison.current_total,
        suppressed_count=suppressed,
        new_count=comparison.new_count,
        growth_keys=comparison.growth_keys,
    ).to_dict()
    filtered = GitMergeTerminologyReport(
        root=report.root,
        lexicon_path=report.lexicon_path,
        base=report.base,
        head=report.head,
        diff_ref=report.diff_ref,
        added_lines=report.added_lines,
        known_occurrences=tuple(visible_known),
        unknown_identifiers=tuple(visible_unknown),
        metadata=metadata,
    )
    return filtered, BaselineFilterResult(
        baseline_total=comparison.baseline_total,
        current_total=comparison.current_total,
        suppressed_count=suppressed,
        new_count=comparison.new_count,
        growth_keys=comparison.growth_keys,
    )


def collect_repository_issues(
    lexicon: Lexicon,
    *,
    root: str | Path,
    paths: Sequence[str] | None = None,
    min_confidence: float = 0.42,
) -> tuple[BaselineIssue, ...]:
    """Collect reviewable terminology issues from current files."""
    root_path = Path(root).resolve()
    files = _repository_files(root_path, paths=paths)
    added_lines: list[GitDiffAddedLine] = []
    for relative_path in files:
        path = root_path / relative_path
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            added_lines.append(GitDiffAddedLine(path=relative_path, line_number=line_number, text=line))
    report = build_git_merge_terminology_report(
        lexicon,
        added_lines,
        root=root_path,
        diff_ref="baseline-scan",
        include_deprecated=True,
        min_confidence=min_confidence,
        include_unresolved_unknowns=False,
    )
    return issues_from_merge_report(report)


def changed_paths_from_merge_report(report: GitMergeTerminologyReport) -> tuple[str, ...]:
    """Return sorted paths mentioned in a merge report."""
    return tuple(sorted({line.path for line in report.added_lines}))


def changed_paths_from_lint_findings(findings: Iterable[LintFinding]) -> tuple[str, ...]:
    """Return sorted paths mentioned in lint findings."""
    return tuple(sorted({finding.path for finding in findings}))


def _issues_to_entries(issues: Iterable[BaselineIssue]) -> tuple[BaselineIssue, ...]:
    counts = _count_issues(issues)
    examples: dict[tuple[str, str, str], BaselineIssue] = {}
    for issue in issues:
        examples.setdefault(issue.key, issue)
    entries = [
        BaselineIssue(
            surface=examples[key].surface,
            normalized_surface=key[0],
            file=key[1],
            kind=key[2],
            count=count,
        )
        for key, count in sorted(counts.items(), key=lambda item: (item[0][1], item[0][2], item[0][0]))
    ]
    return tuple(entries)


def _count_issues(issues: Iterable[BaselineIssue]) -> Counter[tuple[str, str, str]]:
    counts: Counter[tuple[str, str, str]] = Counter()
    for issue in issues:
        counts[issue.key] += issue.count
    return counts


def _kind_from_lint_level(level: int) -> str:
    if level == 1:
        return "deprecated"
    if level == 2:
        return "likely_alias"
    return "likely_new_term"


def _repository_files(root: Path, *, paths: Sequence[str] | None = None) -> tuple[str, ...]:
    if paths:
        files: list[str] = []
        for raw in paths:
            if not raw:
                continue
            candidate = root / raw
            if candidate.is_file() and _is_scannable(candidate):
                files.append(candidate.relative_to(root).as_posix())
            elif candidate.is_dir():
                for path in candidate.rglob("*"):
                    if path.is_file() and _is_scannable(path):
                        files.append(path.relative_to(root).as_posix())
        return tuple(sorted(set(files)))

    git_files = _git_ls_files(root)
    if git_files:
        return tuple(
            path for path in git_files
            if _is_scannable(root / path) and not _is_internal_project_file(path)
        )
    files = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if ".git" in path.parts or ".agent-lexicon" in path.parts:
            continue
        relative = path.relative_to(root).as_posix()
        if _is_internal_project_file(relative):
            continue
        if _is_scannable(path):
            files.append(relative)
    return tuple(sorted(files))


def _git_ls_files(root: Path) -> tuple[str, ...]:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "ls-files"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        return ()
    if completed.returncode != 0:
        return ()
    return tuple(line.strip() for line in completed.stdout.splitlines() if line.strip())


def _is_internal_project_file(relative_path: str) -> bool:
    parts = Path(relative_path).parts
    return bool(parts and parts[0] in {"lexicon", ".agent-lexicon"})


def _is_scannable(path: Path) -> bool:
    name = path.name
    if name.startswith("."):
        return False
    return path.suffix.lower() in _BASELINE_SUFFIXES
