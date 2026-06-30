"""Git merge terminology checks for changed project files.

This module turns a git diff into a terminology review report. It keeps the
runtime decision model unchanged: known surfaces are reported as safe terminology
matches, while unknown code-style identifiers are surfaced for review with
optional near-miss hints.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import fnmatch
from pathlib import Path
import re
import subprocess
from typing import Any, Iterable, Mapping, Sequence

from agent_lexicon.core.matcher import SurfaceMatcher
from agent_lexicon.core.models import Lexicon
from agent_lexicon.scout.near_miss import (
    NearMissError,
    NearMissSuggestion,
    discover_unknown_identifier_surfaces,
    suggest_near_misses,
)


class GitMergeCheckError(RuntimeError):
    """Raised when a git merge terminology check cannot be completed."""


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

    @property
    def needs_review(self) -> bool:
        """Return whether this identifier has at least one likely canonical target."""
        return bool(self.suggestions)

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "line_number": self.line_number,
            "surface": self.surface,
            "text": self.text,
            "needs_review": self.needs_review,
            "suggestions": [suggestion.to_dict() for suggestion in self.suggestions],
        }

    def to_text(self) -> str:
        if not self.suggestions:
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
    def needs_review(self) -> tuple[GitMergeUnknownIdentifier, ...]:
        """Return unknown identifiers with near-miss suggestions."""
        return tuple(identifier for identifier in self.unknown_identifiers if identifier.needs_review)

    @property
    def needs_review_count(self) -> int:
        """Return the number of unknown identifiers with likely canonical targets."""
        return len(self.needs_review)

    @property
    def unresolved_unknowns(self) -> tuple[GitMergeUnknownIdentifier, ...]:
        """Return unknown identifiers that did not receive near-miss suggestions."""
        return tuple(identifier for identifier in self.unknown_identifiers if not identifier.needs_review)

    @property
    def unresolved_unknown_count(self) -> int:
        """Return the number of unknown identifiers without near-miss suggestions."""
        return len(self.unresolved_unknowns)

    @property
    def has_review_items(self) -> bool:
        """Return whether the report contains reviewable near-miss items."""
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
            "unresolved_unknown_count": self.unresolved_unknown_count,
            "has_review_items": self.has_review_items,
            "added_lines": [line.to_dict() for line in self.added_lines],
            "known_occurrences": [occurrence.to_dict() for occurrence in self.known_occurrences],
            "needs_review": [identifier.to_dict() for identifier in self.needs_review],
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
            "Summary: "
            f"known={self.known_occurrence_count}, "
            f"needs_review={self.needs_review_count}, "
            f"unresolved_unknown={self.unresolved_unknown_count}",
        ]
        if self.known_occurrences:
            lines.append("Known terminology:")
            for occurrence in self.known_occurrences:
                lines.append(f"- {occurrence.to_text()}")
        if self.needs_review:
            lines.append("Needs review:")
            for identifier in self.needs_review:
                lines.append(f"- {identifier.to_text()}")
                for suggestion in identifier.suggestions[1:]:
                    lines.append(
                        "  alternative: "
                        f"{suggestion.target_term_id} ({suggestion.target_canonical}) "
                        f"confidence={suggestion.confidence:.3f} via {suggestion.matched_surface!r}"
                    )
        if self.unresolved_unknowns:
            lines.append("Unknown identifiers without suggestion:")
            for identifier in self.unresolved_unknowns:
                lines.append(f"- {identifier.to_text()}")
        if not self.known_occurrences and not self.unknown_identifiers:
            lines.append("No terminology surfaces found in added lines.")
        return "\n".join(lines)


_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
_PATH_PREFIXES = ("a/", "b/")


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
    max_suggestions_per_identifier: int = 3,
    min_confidence: float = 0.42,
    include_unresolved_unknowns: bool = False,
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
    added_lines = parse_git_added_lines(diff_text, include_globs=include_globs)
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
                )
                suggestions = near_miss_report.suggestions
            except NearMissError:
                suggestions = ()
            if not suggestions and not include_unresolved_unknowns:
                continue
            unknown_identifiers.append(
                GitMergeUnknownIdentifier(
                    path=line.path,
                    line_number=line.line_number,
                    surface=surface,
                    text=line.text,
                    suggestions=suggestions,
                )
            )

    return GitMergeTerminologyReport(
        root=str(Path(root)),
        lexicon_path=str(Path(lexicon_path)),
        base=base,
        head=head,
        diff_ref=diff_ref or _diff_ref(base=base, head=head),
        added_lines=line_tuple,
        known_occurrences=tuple(sorted(known_occurrences, key=_known_occurrence_sort_key)),
        unknown_identifiers=tuple(sorted(unknown_identifiers, key=_unknown_identifier_sort_key)),
        metadata=metadata or {},
    )


def parse_git_added_lines(diff_text: str, *, include_globs: Sequence[str] | None = None) -> tuple[GitDiffAddedLine, ...]:
    """Parse added lines from a unified git diff produced with or without context."""
    if not isinstance(diff_text, str):
        raise TypeError("diff_text must be a string")
    include_patterns = tuple(pattern.strip() for pattern in (include_globs or ()) if pattern.strip())
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
            if _path_included(current_path, include_patterns):
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
    semantic = metadata.get("semantic_escalation")
    if not isinstance(semantic, Mapping) or semantic.get("recommended") is not True:
        return ""
    reasons = semantic.get("reasons")
    reason_label = ",".join(str(reason) for reason in reasons) if isinstance(reasons, list) else "recommended"
    return f" semantic_escalation={reason_label}"

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


def _path_included(path: str, include_patterns: tuple[str, ...]) -> bool:
    if not include_patterns:
        return True
    return any(fnmatch.fnmatch(path, pattern) for pattern in include_patterns)


def _known_occurrence_sort_key(occurrence: GitMergeKnownOccurrence) -> tuple[str, int, str, str]:
    return (occurrence.path, occurrence.line_number, occurrence.term_id, occurrence.matched_text)


def _unknown_identifier_sort_key(identifier: GitMergeUnknownIdentifier) -> tuple[str, int, str]:
    return (identifier.path, identifier.line_number, identifier.surface)


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
    "GitMergeTerminologyReport",
    "GitMergeUnknownIdentifier",
    "build_git_merge_terminology_report",
    "check_git_merge_terminology",
    "parse_git_added_lines",
]
