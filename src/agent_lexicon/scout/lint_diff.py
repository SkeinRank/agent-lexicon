"""During-task terminology lint over a working diff.

``lint-diff`` reads a unified diff — from stdin, the working tree, or the
staged index — and checks the added lines against a lexicon. Unlike
``check-merge`` (which compares two committed refs for CI), this is meant to be
run by an agent or a developer *while working*, before anything is committed.

The check is layered by how much the tool can honestly assert:

- Level 1 (deprecated): a declared deprecated term/alias was used. This is an
  enforcement error — deterministic and reproducible. Severity: ``fail``.
- Level 2 (near-miss): an unknown identifier is lexically close to a canonical
  term (typo / case / separator drift). Severity: ``warn`` (``fail`` in strict).
- Level 3 (unknown): an unknown project-like term with no declared mapping.
  Reported for awareness only; never an error. Severity: ``info``.

Everything probabilistic (semantic overlap) stays suggestion-only and never
changes the exit code on its own.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from agent_lexicon.core import Lexicon
from agent_lexicon.scout.git_merge import (
    GitMergeCheckError,
    GitMergeReviewKind,
    build_git_merge_terminology_report,
    parse_git_added_lines,
)
from agent_lexicon.scout.near_miss import SemanticNearMissBackend
from agent_lexicon.ingest.local import load_gitignore_rules


class LintDiffError(Exception):
    """Raised when a lint-diff run cannot be completed."""


@dataclass(frozen=True, slots=True)
class LintFinding:
    """One terminology finding from a working diff."""

    level: int
    severity: str  # "fail" | "warn" | "info"
    path: str
    line_number: int
    surface: str
    canonical: str  # target canonical term, when known
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "severity": self.severity,
            "path": self.path,
            "line_number": self.line_number,
            "surface": self.surface,
            "canonical": self.canonical,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class LintDiffReport:
    """Result of linting a working diff against a lexicon."""

    scanned_file_count: int
    added_line_count: int
    findings: tuple[LintFinding, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def fail_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "fail")

    @property
    def warn_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "warn")

    @property
    def info_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "info")

    def exit_code(self, *, strict: bool) -> int:
        """0 when clean; 1 when any fail (or any warn under strict)."""
        if self.fail_count:
            return 1
        if strict and self.warn_count:
            return 1
        return 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "scanned_file_count": self.scanned_file_count,
            "added_line_count": self.added_line_count,
            "fail_count": self.fail_count,
            "warn_count": self.warn_count,
            "info_count": self.info_count,
            "findings": [f.to_dict() for f in self.findings],
            "metadata": dict(self.metadata),
        }

    def to_text(self, *, strict: bool) -> str:
        lines = [
            f"Terminology lint: {self.scanned_file_count} files, "
            f"{self.added_line_count} added lines"
        ]
        fails = [f for f in self.findings if f.severity == "fail"]
        warns = [f for f in self.findings if f.severity == "warn"]
        infos = [f for f in self.findings if f.severity == "info"]

        if fails:
            lines.append("")
            lines.append("Deprecated terms (fail):")
            for f in fails:
                lines.append(f"- {f.path}:{f.line_number} {f.surface} -> use \"{f.canonical}\"")
        if warns:
            lines.append("")
            label = "fail" if strict else "warn"
            lines.append(f"Possible typos / near-misses ({label}):")
            for f in warns:
                lines.append(f"- {f.path}:{f.line_number} {f.surface} -> did you mean \"{f.canonical}\"?")
        if infos:
            lines.append("")
            lines.append("New project terms (info):")
            for f in infos:
                hint = f" (possibly related: {f.canonical})" if f.canonical else ""
                lines.append(f"- {f.path}:{f.line_number} {f.surface}{hint}")

        if not self.findings:
            lines.append("No terminology issues found.")
        return "\n".join(lines)


def _run_working_diff(root: Path, *, staged: bool, git_executable: str) -> str:
    command = [git_executable, "-C", str(root), "diff", "--unified=0", "--no-color", "--diff-filter=ACMR"]
    if staged:
        command.append("--staged")
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
        raise LintDiffError(f"unable to run git: {exc}") from exc
    if completed.returncode != 0:
        stderr = completed.stderr.strip() or "git diff failed"
        raise LintDiffError(f"git diff failed: {stderr}")
    return completed.stdout


def lint_working_diff(
    lexicon: Lexicon,
    *,
    diff_text: str | None = None,
    root: str | Path = ".",
    staged: bool = False,
    scopes: Iterable[str] | None = None,
    include_globs: Sequence[str] | None = None,
    exclude_globs: Sequence[str] | None = None,
    respect_gitignore: bool = True,
    min_confidence: float = 0.42,
    semantic_backend: SemanticNearMissBackend | None = None,
    git_executable: str = "git",
) -> LintDiffReport:
    """Lint a working diff (stdin text, working tree, or staged) against a lexicon."""
    if not isinstance(lexicon, Lexicon):
        raise LintDiffError("lexicon must be a Lexicon")
    root_path = Path(root).resolve()
    if diff_text is None:
        if not root_path.exists():
            raise LintDiffError(f"root does not exist: {root_path}")
        diff_text = _run_working_diff(root_path, staged=staged, git_executable=git_executable)

    gitignore_rules = load_gitignore_rules(root_path) if respect_gitignore and root_path.exists() else ()
    try:
        added_lines = parse_git_added_lines(
            diff_text,
            include_globs=include_globs,
            exclude_globs=exclude_globs,
            gitignore_rules=gitignore_rules,
        )
        report = build_git_merge_terminology_report(
            lexicon,
            added_lines,
            root=root_path,
            base="WORKING",
            head="WORKING",
            diff_ref="working-tree",
            scopes=scopes,
            include_deprecated=True,
            min_confidence=min_confidence,
            include_unresolved_unknowns=False,
            semantic_backend=semantic_backend,
        )
    except GitMergeCheckError as exc:
        raise LintDiffError(str(exc)) from exc

    findings: list[LintFinding] = []

    # Level 1 — deprecated declared terms used in the diff.
    for occ in report.known_occurrences:
        if occ.deprecated and occ.matched_text != occ.canonical:
            findings.append(
                LintFinding(
                    level=1,
                    severity="fail",
                    path=occ.path,
                    line_number=occ.line_number,
                    surface=occ.matched_text,
                    canonical=occ.canonical,
                    message=f"deprecated term; use canonical \"{occ.canonical}\"",
                )
            )

    # Level 2 — unknown identifiers that lexically near-miss a canonical term.
    for identifier in report.likely_aliases:
        if identifier.suggestions:
            best = identifier.suggestions[0]
            findings.append(
                LintFinding(
                    level=2,
                    severity="warn",
                    path=identifier.path,
                    line_number=identifier.line_number,
                    surface=identifier.surface,
                    canonical=best.target_canonical,
                    message=f"near-miss of canonical \"{best.target_canonical}\"",
                )
            )

    # Level 3 — genuinely unknown project-like terms (report only).
    for identifier in report.likely_new_terms:
        related = identifier.suggestions[0].target_canonical if identifier.suggestions else ""
        findings.append(
            LintFinding(
                level=3,
                severity="info",
                path=identifier.path,
                line_number=identifier.line_number,
                surface=identifier.surface,
                canonical=related,
                message="new project term; no declared mapping",
            )
        )

    findings.sort(key=lambda f: (f.level, f.path, f.line_number))
    return LintDiffReport(
        scanned_file_count=report.scanned_file_count,
        added_line_count=report.added_line_count,
        findings=tuple(findings),
        metadata={"source": "working_diff", "staged": bool(staged)},
    )
