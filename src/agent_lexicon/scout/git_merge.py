"""Git merge terminology checks for changed project files.

This module turns a git diff into a terminology review report. It keeps the
runtime decision model unchanged: known surfaces are reported as safe terminology
matches, while unknown code-style identifiers are surfaced for review with
optional near-miss hints.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import fnmatch
from pathlib import Path
import re
import subprocess
from typing import Any, Iterable, Mapping, Sequence

from agent_lexicon.core.matcher import SurfaceMatcher
from agent_lexicon.core.models import Lexicon
from agent_lexicon.core.snapshots import lexicon_runtime_metadata
from agent_lexicon.scout.near_miss import (
    NearMissError,
    NearMissSuggestion,
    discover_unknown_identifier_surfaces,
    suggest_near_misses,
)
from agent_lexicon.scout.semantic import SemanticNearMissBackend
from agent_lexicon.text import surface_fragments


class GitMergeCheckError(RuntimeError):
    """Raised when a git merge terminology check cannot be completed."""


class GitMergeReviewKind(str, Enum):
    """Review class for an unknown identifier found in a merge diff."""

    LIKELY_ALIAS = "likely_alias"
    LIKELY_NEW_TERM = "likely_new_term"
    UNRESOLVED_IDENTIFIER = "unresolved_identifier"


@dataclass(frozen=True, slots=True)
class GitDiffAddedLine:
    """One added line from a git diff."""

    path: str
    line_number: int
    text: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _clean_text(self.path, field_name="path"))
        if self.line_number < 1:
            raise GitMergeCheckError("line_number must be greater than 0")
        if not isinstance(self.text, str):
            raise GitMergeCheckError("text must be a string")

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "line_number": self.line_number,
            "text": self.text,
        }


@dataclass(frozen=True, slots=True)
class GitMergeKnownOccurrence:
    """A known terminology surface found in an added git diff line."""

    path: str
    line_number: int
    surface: str
    matched_text: str
    term_id: str
    canonical: str
    scopes: tuple[str, ...] = ()
    deprecated: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _clean_text(self.path, field_name="path"))
        if self.line_number < 1:
            raise GitMergeCheckError("line_number must be greater than 0")
        object.__setattr__(self, "surface", _clean_text(self.surface, field_name="surface"))
        object.__setattr__(self, "matched_text", _clean_text(self.matched_text, field_name="matched_text"))
        object.__setattr__(self, "term_id", _clean_text(self.term_id, field_name="term_id"))
        object.__setattr__(self, "canonical", _clean_text(self.canonical, field_name="canonical"))
        object.__setattr__(self, "scopes", _clean_tuple(self.scopes, field_name="scopes"))
        if not isinstance(self.deprecated, bool):
            raise GitMergeCheckError("deprecated must be a boolean")

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "line_number": self.line_number,
            "surface": self.surface,
            "matched_text": self.matched_text,
            "term_id": self.term_id,
            "canonical": self.canonical,
            "scopes": list(self.scopes),
            "deprecated": self.deprecated,
        }

    def to_text(self) -> str:
        scope_label = f" scopes={','.join(self.scopes)}" if self.scopes else ""
        deprecated_label = " deprecated" if self.deprecated else ""
        return (
            f"{self.path}:{self.line_number} {self.matched_text!r} -> "
            f"{self.term_id} ({self.canonical}){scope_label}{deprecated_label}"
        )


@dataclass(frozen=True, slots=True)
class GitMergeUnknownIdentifier:
    """An unknown code-style identifier found in an added git diff line."""

    path: str
    line_number: int
    surface: str
    text: str
    suggestions: tuple[NearMissSuggestion, ...] = ()
    review_kind: GitMergeReviewKind = GitMergeReviewKind.LIKELY_ALIAS

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _clean_text(self.path, field_name="path"))
        if self.line_number < 1:
            raise GitMergeCheckError("line_number must be greater than 0")
        object.__setattr__(self, "surface", _clean_text(self.surface, field_name="surface"))
        if not isinstance(self.text, str):
            raise GitMergeCheckError("text must be a string")
        if not isinstance(self.suggestions, tuple):
            object.__setattr__(self, "suggestions", tuple(self.suggestions))
        for suggestion in self.suggestions:
            if not isinstance(suggestion, NearMissSuggestion):
                raise GitMergeCheckError("suggestions must contain NearMissSuggestion objects")
        try:
            review_kind = GitMergeReviewKind(
                self.review_kind.value if isinstance(self.review_kind, GitMergeReviewKind) else str(self.review_kind)
            )
        except ValueError as exc:
            raise GitMergeCheckError("review_kind must be a GitMergeReviewKind value") from exc
        if self.suggestions and review_kind != GitMergeReviewKind.LIKELY_ALIAS:
            raise GitMergeCheckError("identifiers with suggestions must use likely_alias review_kind")
        if not self.suggestions and review_kind == GitMergeReviewKind.LIKELY_ALIAS:
            review_kind = GitMergeReviewKind.LIKELY_NEW_TERM
        object.__setattr__(self, "review_kind", review_kind)

    @property
    def needs_review(self) -> bool:
        """Return whether this identifier should be shown in the merge review."""
        return self.review_kind in {GitMergeReviewKind.LIKELY_ALIAS, GitMergeReviewKind.LIKELY_NEW_TERM}

    @property
    def has_near_miss(self) -> bool:
        """Return whether this identifier has at least one likely canonical target."""
        return bool(self.suggestions)

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "line_number": self.line_number,
            "surface": self.surface,
            "text": self.text,
            "needs_review": self.needs_review,
            "review_kind": self.review_kind.value,
            "suggestions": [suggestion.to_dict() for suggestion in self.suggestions],
        }

    def to_text(self) -> str:
        if not self.suggestions:
            if self.review_kind == GitMergeReviewKind.LIKELY_NEW_TERM:
                return f"{self.path}:{self.line_number} {self.surface!r} unknown; possible new term"
            return f"{self.path}:{self.line_number} {self.surface!r} unknown"
        best = self.suggestions[0]
        semantic_label = _semantic_escalation_label(best.metadata)
        return (
            f"{self.path}:{self.line_number} {self.surface!r} unknown; "
            f"near miss: {best.target_term_id} ({best.target_canonical}) "
            f"confidence={best.confidence:.3f} via {best.matched_surface!r}{semantic_label}"
        )


@dataclass(frozen=True, slots=True)
class GitMergeTerminologyReport:
    """Terminology report for added lines in a git merge or pull request diff."""

    root: str
    lexicon_path: str
    base: str
    head: str
    diff_ref: str
    added_lines: tuple[GitDiffAddedLine, ...] = ()
    known_occurrences: tuple[GitMergeKnownOccurrence, ...] = ()
    unknown_identifiers: tuple[GitMergeUnknownIdentifier, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", _clean_text(self.root, field_name="root"))
        object.__setattr__(self, "lexicon_path", _clean_text(self.lexicon_path, field_name="lexicon_path"))
        object.__setattr__(self, "base", _clean_text(self.base, field_name="base"))
        object.__setattr__(self, "head", _clean_text(self.head, field_name="head"))
        object.__setattr__(self, "diff_ref", _clean_text(self.diff_ref, field_name="diff_ref"))
        if not isinstance(self.added_lines, tuple):
            object.__setattr__(self, "added_lines", tuple(self.added_lines))
        if not isinstance(self.known_occurrences, tuple):
            object.__setattr__(self, "known_occurrences", tuple(self.known_occurrences))
        if not isinstance(self.unknown_identifiers, tuple):
            object.__setattr__(self, "unknown_identifiers", tuple(self.unknown_identifiers))
        for line in self.added_lines:
            if not isinstance(line, GitDiffAddedLine):
                raise GitMergeCheckError("added_lines must contain GitDiffAddedLine objects")
        for occurrence in self.known_occurrences:
            if not isinstance(occurrence, GitMergeKnownOccurrence):
                raise GitMergeCheckError("known_occurrences must contain GitMergeKnownOccurrence objects")
        for identifier in self.unknown_identifiers:
            if not isinstance(identifier, GitMergeUnknownIdentifier):
                raise GitMergeCheckError("unknown_identifiers must contain GitMergeUnknownIdentifier objects")
        if not isinstance(self.metadata, Mapping):
            raise GitMergeCheckError("metadata must be a mapping")
        object.__setattr__(self, "metadata", {str(key): value for key, value in self.metadata.items()})

    @property
    def scanned_file_count(self) -> int:
        """Return the number of files with added lines that were scanned."""
        return len({line.path for line in self.added_lines})

    @property
    def added_line_count(self) -> int:
        """Return the number of added lines scanned."""
        return len(self.added_lines)

    @property
    def known_occurrence_count(self) -> int:
        """Return the number of known terminology occurrences found."""
        return len(self.known_occurrences)

    @property
    def unknown_identifier_count(self) -> int:
        """Return the number of unknown identifier occurrences found."""
        return len(self.unknown_identifiers)

    @property
    def likely_aliases(self) -> tuple[GitMergeUnknownIdentifier, ...]:
        """Return unknown identifiers that look like aliases for existing terms."""
        return tuple(
            identifier
            for identifier in self.unknown_identifiers
            if identifier.review_kind == GitMergeReviewKind.LIKELY_ALIAS
        )

    @property
    def likely_alias_count(self) -> int:
        """Return the number of likely alias review items."""
        return len(self.likely_aliases)

    @property
    def likely_new_terms(self) -> tuple[GitMergeUnknownIdentifier, ...]:
        """Return unknown identifiers that may represent new terminology."""
        return tuple(
            identifier
            for identifier in self.unknown_identifiers
            if identifier.review_kind == GitMergeReviewKind.LIKELY_NEW_TERM
        )

    @property
    def likely_new_term_count(self) -> int:
        """Return the number of possible new-term review items."""
        return len(self.likely_new_terms)

    @property
    def needs_review(self) -> tuple[GitMergeUnknownIdentifier, ...]:
        """Return unknown identifiers shown in the default merge review."""
        return tuple(identifier for identifier in self.unknown_identifiers if identifier.needs_review)

    @property
    def needs_review_count(self) -> int:
        """Return the number of reviewable unknown identifiers."""
        return len(self.needs_review)

    @property
    def unresolved_unknowns(self) -> tuple[GitMergeUnknownIdentifier, ...]:
        """Return low-signal unknown identifiers included only for full audits."""
        return tuple(
            identifier
            for identifier in self.unknown_identifiers
            if identifier.review_kind == GitMergeReviewKind.UNRESOLVED_IDENTIFIER
        )

    @property
    def unresolved_unknown_count(self) -> int:
        """Return the number of low-signal unknown identifiers included in the report."""
        return len(self.unresolved_unknowns)

    @property
    def hidden_unresolved_count(self) -> int:
        """Return the number of low-signal unknown identifiers hidden from the default report."""
        raw_count = self.metadata.get("hidden_unresolved_count", 0)
        try:
            count = int(raw_count)
        except (TypeError, ValueError):
            return 0
        return max(count, 0)

    @property
    def has_review_items(self) -> bool:
        """Return whether the report contains items that should be reviewed."""
        return self.needs_review_count > 0

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable report payload."""
        return {
            "root": self.root,
            "lexicon_path": self.lexicon_path,
            "base": self.base,
            "head": self.head,
            "diff_ref": self.diff_ref,
            "scanned_file_count": self.scanned_file_count,
            "added_line_count": self.added_line_count,
            "known_occurrence_count": self.known_occurrence_count,
            "unknown_identifier_count": self.unknown_identifier_count,
            "needs_review_count": self.needs_review_count,
            "likely_alias_count": self.likely_alias_count,
            "likely_new_term_count": self.likely_new_term_count,
            "unresolved_unknown_count": self.unresolved_unknown_count,
            "hidden_unresolved_count": self.hidden_unresolved_count,
            "has_review_items": self.has_review_items,
            "added_lines": [line.to_dict() for line in self.added_lines],
            "known_occurrences": [occurrence.to_dict() for occurrence in self.known_occurrences],
            "needs_review": [identifier.to_dict() for identifier in self.needs_review],
            "likely_aliases": [identifier.to_dict() for identifier in self.likely_aliases],
            "likely_new_terms": [identifier.to_dict() for identifier in self.likely_new_terms],
            "unresolved_unknowns": [identifier.to_dict() for identifier in self.unresolved_unknowns],
            "metadata": dict(self.metadata),
        }

    def to_text(self) -> str:
        """Return a human-readable report."""
        lines = [
            "Git merge terminology check: "
            f"{self.scanned_file_count} files, {self.added_line_count} added lines",
            f"Range: {self.diff_ref}",
            f"Lexicon: {self.lexicon_path}",
            f"Lexicon snapshot: {self.metadata.get('lexicon_snapshot_ref', 'unknown')}",
            "Summary: "
            f"known={self.known_occurrence_count}, "
            f"likely_alias={self.likely_alias_count}, "
            f"likely_new_term={self.likely_new_term_count}, "
            f"unresolved_unknown={self.unresolved_unknown_count}, "
            f"hidden_unresolved={self.hidden_unresolved_count}",
        ]
        if self.known_occurrences:
            lines.append("Known terminology:")
            for occurrence in self.known_occurrences:
                lines.append(f"- {occurrence.to_text()}")
        if self.needs_review:
            lines.append("Needs review:")
        if self.likely_aliases:
            lines.append("Likely aliases:")
            for identifier in self.likely_aliases:
                lines.append(f"- {identifier.to_text()}")
                for suggestion in identifier.suggestions[1:]:
                    lines.append(
                        "  alternative: "
                        f"{suggestion.target_term_id} ({suggestion.target_canonical}) "
                        f"confidence={suggestion.confidence:.3f} via {suggestion.matched_surface!r}"
                    )
        if self.likely_new_terms:
            lines.append("New terminology candidates:")
            for identifier in self.likely_new_terms:
                lines.append(f"- {identifier.to_text()}")
        if self.unresolved_unknowns:
            lines.append("Low-signal unknown identifiers:")
            for identifier in self.unresolved_unknowns:
                lines.append(f"- {identifier.to_text()}")
        if self.hidden_unresolved_count:
            lines.append(
                "Hidden unresolved identifiers: "
                f"{self.hidden_unresolved_count}. "
                "Use --include-unresolved-unknowns to inspect low-signal identifiers."
            )
        if not self.known_occurrences and not self.unknown_identifiers and not self.hidden_unresolved_count:
            lines.append("No terminology surfaces found in added lines.")
        return "\n".join(lines)


_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
_PATH_PREFIXES = ("a/", "b/")
_LOW_SIGNAL_IDENTIFIER_FRAGMENTS = frozenset({
    "actual",
    "arg",
    "args",
    "ctx",
    "dst",
    "entry",
    "entries",
    "expected",
    "file",
    "files",
    "fixture",
    "fixtures",
    "flag",
    "idx",
    "index",
    "issue",
    "issues",
    "item",
    "items",
    "mock",
    "name",
    "names",
    "node",
    "nodes",
    "obj",
    "object",
    "old",
    "param",
    "params",
    "path",
    "result",
    "results",
    "row",
    "rows",
    "src",
    "temp",
    "test",
    "tests",
    "tmp",
    "val",
    "value",
    "values",
    "var",
})


def check_git_merge_terminology(
    lexicon: Lexicon,
    *,
    root: str | Path = ".",
    lexicon_path: str | Path | None = None,
    base: str = "main",
    head: str = "HEAD",
    scopes: Iterable[str] | None = None,
    include_deprecated: bool = True,
    include_globs: Sequence[str] | None = None,
    exclude_globs: Sequence[str] | None = None,
    max_suggestions_per_identifier: int = 3,
    min_confidence: float = 0.42,
    include_unresolved_unknowns: bool = False,
    semantic_backend: SemanticNearMissBackend | None = None,
    git_executable: str = "git",
) -> GitMergeTerminologyReport:
    """Run a terminology check over added lines between two git refs.

    The default diff range is ``base...head``, which matches the pull-request
    style merge-base comparison most teams expect.
    """
    if not isinstance(lexicon, Lexicon):
        raise GitMergeCheckError("lexicon must be a Lexicon")
    root_path = Path(root).resolve()
    if not root_path.exists():
        raise GitMergeCheckError(f"root does not exist: {root_path}")
    diff_ref = _diff_ref(base=base, head=head)
    diff_text = _run_git_diff(root_path, diff_ref=diff_ref, git_executable=git_executable)
    added_lines = parse_git_added_lines(diff_text, include_globs=include_globs, exclude_globs=exclude_globs)
    return build_git_merge_terminology_report(
        lexicon,
        added_lines,
        root=root_path,
        lexicon_path=lexicon_path if lexicon_path is not None else "lexicon/lexicon.yaml",
        base=base,
        head=head,
        diff_ref=diff_ref,
        scopes=scopes,
        include_deprecated=include_deprecated,
        max_suggestions_per_identifier=max_suggestions_per_identifier,
        min_confidence=min_confidence,
        include_unresolved_unknowns=include_unresolved_unknowns,
        semantic_backend=semantic_backend,
        metadata={"source": "git_diff"},
    )


def build_git_merge_terminology_report(
    lexicon: Lexicon,
    added_lines: Iterable[GitDiffAddedLine],
    *,
    root: str | Path = ".",
    lexicon_path: str | Path = "lexicon/lexicon.yaml",
    base: str = "main",
    head: str = "HEAD",
    diff_ref: str | None = None,
    scopes: Iterable[str] | None = None,
    include_deprecated: bool = True,
    max_suggestions_per_identifier: int = 3,
    min_confidence: float = 0.42,
    include_unresolved_unknowns: bool = False,
    semantic_backend: SemanticNearMissBackend | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> GitMergeTerminologyReport:
    """Build a terminology report from already parsed added git diff lines."""
    if not isinstance(lexicon, Lexicon):
        raise GitMergeCheckError("lexicon must be a Lexicon")
    max_suggestions_per_identifier = int(max_suggestions_per_identifier)
    if max_suggestions_per_identifier < 1:
        raise GitMergeCheckError("max_suggestions_per_identifier must be greater than 0")
    min_confidence = float(min_confidence)
    if not 0.0 <= min_confidence <= 1.0:
        raise GitMergeCheckError("min_confidence must be between 0.0 and 1.0")

    line_tuple = tuple(added_lines)
    matcher = SurfaceMatcher.from_lexicon(lexicon, include_deprecated=include_deprecated)
    known_occurrences: list[GitMergeKnownOccurrence] = []
    unknown_identifiers: list[GitMergeUnknownIdentifier] = []
    hidden_unresolved_count = 0
    seen_known: set[tuple[str, int, str, str]] = set()
    seen_unknown: set[tuple[str, int, str]] = set()

    for line in line_tuple:
        for match in matcher.match(line.text, scopes=scopes, include_deprecated=include_deprecated, longest_only=True):
            term = lexicon.get_term(match.term_id)
            if term is None:
                continue
            key = (line.path, line.line_number, match.term_id, match.matched_text)
            if key in seen_known:
                continue
            seen_known.add(key)
            known_occurrences.append(
                GitMergeKnownOccurrence(
                    path=line.path,
                    line_number=line.line_number,
                    surface=match.surface,
                    matched_text=match.matched_text,
                    term_id=match.term_id,
                    canonical=term.canonical,
                    scopes=match.scopes or term.scopes,
                    deprecated=term.deprecated or match.deprecated,
                )
            )

        for surface in discover_unknown_identifier_surfaces(line.text, max_surfaces=25):
            if not _is_merge_identifier_surface(surface):
                continue
            if _surface_is_known(surface, matcher, scopes=scopes, include_deprecated=include_deprecated):
                continue
            key = (line.path, line.line_number, surface)
            if key in seen_unknown:
                continue
            seen_unknown.add(key)
            try:
                near_miss_report = suggest_near_misses(
                    lexicon,
                    surface,
                    scopes=scopes,
                    include_deprecated=include_deprecated,
                    max_suggestions=max_suggestions_per_identifier,
                    min_confidence=min_confidence,
                    semantic_backend=semantic_backend,
                )
                suggestions = near_miss_report.suggestions
            except NearMissError:
                suggestions = ()
            review_kind = _unknown_review_kind(surface, text=line.text, suggestions=suggestions)
            if review_kind == GitMergeReviewKind.UNRESOLVED_IDENTIFIER and not include_unresolved_unknowns:
                hidden_unresolved_count += 1
                continue
            unknown_identifiers.append(
                GitMergeUnknownIdentifier(
                    path=line.path,
                    line_number=line.line_number,
                    surface=surface,
                    text=line.text,
                    suggestions=suggestions,
                    review_kind=review_kind,
                )
            )

    report_metadata: dict[str, Any] = dict(metadata or {})
    report_metadata.update(lexicon_runtime_metadata(lexicon, source_path=lexicon_path))
    report_metadata["include_unresolved_unknowns"] = include_unresolved_unknowns
    report_metadata["hidden_unresolved_count"] = hidden_unresolved_count

    return GitMergeTerminologyReport(
        root=str(Path(root)),
        lexicon_path=str(Path(lexicon_path)),
        base=base,
        head=head,
        diff_ref=diff_ref or _diff_ref(base=base, head=head),
        added_lines=line_tuple,
        known_occurrences=tuple(sorted(known_occurrences, key=_known_occurrence_sort_key)),
        unknown_identifiers=tuple(sorted(unknown_identifiers, key=_unknown_identifier_sort_key)),
        metadata=report_metadata,
    )


def parse_git_added_lines(
    diff_text: str,
    *,
    include_globs: Sequence[str] | None = None,
    exclude_globs: Sequence[str] | None = None,
) -> tuple[GitDiffAddedLine, ...]:
    """Parse added lines from a unified git diff produced with or without context."""
    if not isinstance(diff_text, str):
        raise TypeError("diff_text must be a string")
    include_patterns = tuple(pattern.strip() for pattern in (include_globs or ()) if pattern.strip())
    exclude_patterns = tuple(pattern.strip() for pattern in (exclude_globs or ()) if pattern.strip())
    added_lines: list[GitDiffAddedLine] = []
    current_path: str | None = None
    current_new_line: int | None = None
    in_hunk = False

    for raw_line in diff_text.splitlines():
        if raw_line.startswith("diff --git "):
            current_path = None
            current_new_line = None
            in_hunk = False
            continue
        if raw_line.startswith("+++ "):
            current_path = _parse_new_file_path(raw_line[4:].strip())
            continue
        hunk_match = _HUNK_RE.match(raw_line)
        if hunk_match:
            current_new_line = int(hunk_match.group(1))
            in_hunk = True
            continue
        if not in_hunk or current_path is None or current_new_line is None:
            continue
        if raw_line.startswith("+") and not raw_line.startswith("+++"):
            if _path_selected(current_path, include_patterns, exclude_patterns):
                added_lines.append(
                    GitDiffAddedLine(
                        path=current_path,
                        line_number=current_new_line,
                        text=raw_line[1:],
                    )
                )
            current_new_line += 1
            continue
        if raw_line.startswith("-") and not raw_line.startswith("---"):
            continue
        if raw_line.startswith("\\"):
            continue
        current_new_line += 1

    return tuple(added_lines)


def _semantic_escalation_label(metadata: Mapping[str, Any]) -> str:
    labels: list[str] = []
    if metadata.get("semantic_applied") is True:
        backend = metadata.get("semantic_backend") or metadata.get("semantic_model") or "semantic"
        score = metadata.get("semantic_score")
        try:
            labels.append(f"semantic_score={float(score):.3f}")
        except (TypeError, ValueError):
            labels.append("semantic_score=n/a")
        labels.append(f"semantic_backend={backend}")
    semantic = metadata.get("semantic_escalation")
    if isinstance(semantic, Mapping) and semantic.get("recommended") is True:
        reasons = semantic.get("reasons")
        reason_label = ",".join(str(reason) for reason in reasons) if isinstance(reasons, list) else "recommended"
        labels.append(f"semantic_escalation={reason_label}")
    return " " + " ".join(labels) if labels else ""


def _run_git_diff(root: Path, *, diff_ref: str, git_executable: str) -> str:
    command = [
        git_executable,
        "-C",
        str(root),
        "diff",
        "--unified=0",
        "--no-color",
        "--diff-filter=ACMR",
        diff_ref,
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        raise GitMergeCheckError(f"unable to run git: {exc}") from exc
    if completed.returncode != 0:
        stderr = completed.stderr.strip() or "git diff failed"
        raise GitMergeCheckError(stderr)
    return completed.stdout


def _diff_ref(*, base: str, head: str) -> str:
    base_clean = _clean_ref(base, field_name="base")
    head_clean = _clean_ref(head, field_name="head")
    return f"{base_clean}...{head_clean}"


def _clean_ref(value: str, *, field_name: str) -> str:
    cleaned = _clean_text(value, field_name=field_name)
    if any(char.isspace() for char in cleaned):
        raise GitMergeCheckError(f"{field_name} must not contain whitespace")
    return cleaned


def _surface_is_known(
    surface: str,
    matcher: SurfaceMatcher,
    *,
    scopes: Iterable[str] | None,
    include_deprecated: bool,
) -> bool:
    matches = matcher.match(surface, scopes=scopes, include_deprecated=include_deprecated, longest_only=True)
    return any(match.start == 0 and match.end == len(surface) for match in matches)


def _parse_new_file_path(path_token: str) -> str | None:
    if path_token == "/dev/null":
        return None
    path = path_token
    if "\t" in path:
        path = path.split("\t", 1)[0]
    if path.startswith('"') and path.endswith('"'):
        path = path[1:-1]
    for prefix in _PATH_PREFIXES:
        if path.startswith(prefix):
            path = path[len(prefix):]
            break
    return path or None


def _path_selected(path: str, include_patterns: tuple[str, ...], exclude_patterns: tuple[str, ...]) -> bool:
    normalized = path.replace("\\", "/")
    included = True if not include_patterns else any(fnmatch.fnmatch(normalized, pattern) for pattern in include_patterns)
    if not included:
        return False
    return not _path_excluded(normalized, exclude_patterns)


def _path_excluded(path: str, exclude_patterns: tuple[str, ...]) -> bool:
    if not exclude_patterns:
        return False
    name = Path(path).name
    parts = set(Path(path).parts)
    for raw_pattern in exclude_patterns:
        pattern = raw_pattern.strip().replace("\\", "/")
        if not pattern:
            continue
        if pattern.startswith("/"):
            pattern = pattern[1:]
        if pattern.endswith("/"):
            pattern = pattern + "**"
        if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(name, pattern):
            return True
        if "/" not in pattern and not any(char in pattern for char in "*?[") and pattern in parts:
            return True
    return False


def _known_occurrence_sort_key(occurrence: GitMergeKnownOccurrence) -> tuple[str, int, str, str]:
    return (occurrence.path, occurrence.line_number, occurrence.term_id, occurrence.matched_text)


def _unknown_identifier_sort_key(identifier: GitMergeUnknownIdentifier) -> tuple[str, int, str, str]:
    return (identifier.path, identifier.line_number, identifier.review_kind.value, identifier.surface)


def _is_merge_identifier_surface(surface: str) -> bool:
    if not isinstance(surface, str) or not surface.strip():
        return False
    if any(char.isspace() for char in surface):
        return False
    return not any(char in surface for char in "(){}[]='\"")


def _unknown_review_kind(
    surface: str,
    *,
    text: str,
    suggestions: tuple[NearMissSuggestion, ...],
) -> GitMergeReviewKind:
    if suggestions:
        return GitMergeReviewKind.LIKELY_ALIAS
    if _is_low_signal_unknown_identifier(surface) or _is_call_like_unknown_identifier(surface, text):
        return GitMergeReviewKind.UNRESOLVED_IDENTIFIER
    return GitMergeReviewKind.LIKELY_NEW_TERM


def _is_low_signal_unknown_identifier(surface: str) -> bool:
    fragments = tuple(fragment.lower() for fragment in surface_fragments(surface) if fragment.strip())
    if not fragments:
        return True
    if any(len(fragment) == 1 for fragment in fragments):
        return True
    return all(fragment in _LOW_SIGNAL_IDENTIFIER_FRAGMENTS for fragment in fragments)


def _is_call_like_unknown_identifier(surface: str, text: str) -> bool:
    if not surface or not text:
        return False
    pattern = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(surface) + r"(?![A-Za-z0-9_])")
    matches = tuple(pattern.finditer(text))
    if not matches:
        return False
    for match in matches:
        tail = text[match.end():].lstrip()
        if not tail.startswith("("):
            return False
    return True


def _clean_text(value: str, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise GitMergeCheckError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise GitMergeCheckError(f"{field_name} must not be empty")
    return cleaned


def _clean_tuple(values: tuple[str, ...] | Iterable[str], *, field_name: str) -> tuple[str, ...]:
    if isinstance(values, str):
        raise GitMergeCheckError(f"{field_name} must be an iterable of strings")
    cleaned: list[str] = []
    for value in values:
        cleaned.append(_clean_text(str(value), field_name=f"{field_name} item"))
    return tuple(cleaned)


__all__ = [
    "GitDiffAddedLine",
    "GitMergeCheckError",
    "GitMergeKnownOccurrence",
    "GitMergeReviewKind",
    "GitMergeTerminologyReport",
    "GitMergeUnknownIdentifier",
    "build_git_merge_terminology_report",
    "check_git_merge_terminology",
    "parse_git_added_lines",
]
