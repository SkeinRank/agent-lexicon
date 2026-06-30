"""Lexicon quality checks for safer terminology governance.

The linter complements structural validation. Loading rejects invalid documents;
linting highlights surfaces that are valid but likely to create noisy matches,
unsafe tool routing, or confusing review behavior in real projects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from agent_lexicon.text import code_identifier_variants, normalize_text_for_matching, surface_fragments

from .loader import AgentLexiconLoadError, _normalize_document_format, _resolve_document_format, load_lexicon
from .models import Alias, Lexicon, Term


class LexiconLintSeverity(str, Enum):
    """Severity levels produced by the lexicon linter."""

    WARNING = "warning"


class LexiconLintCode(str, Enum):
    """Stable linter finding codes."""

    MISSING_VERSION = "missing_version"
    SHORT_SURFACE = "short_surface"
    BROAD_SURFACE = "broad_surface"
    DEPRECATED_BROAD_SURFACE = "deprecated_broad_surface"
    TOOL_BROAD_SURFACE = "tool_broad_surface"
    NORMALIZED_SURFACE_COLLISION = "normalized_surface_collision"


@dataclass(frozen=True, slots=True)
class LexiconLintFinding:
    """A single reviewable lexicon quality finding."""

    code: LexiconLintCode
    message: str
    severity: LexiconLintSeverity = LexiconLintSeverity.WARNING
    term_id: str | None = None
    surface: str | None = None
    scopes: tuple[str, ...] = ()
    hint: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable finding payload."""
        return {
            "severity": self.severity.value,
            "code": self.code.value,
            "message": self.message,
            "term_id": self.term_id,
            "surface": self.surface,
            "scopes": list(self.scopes),
            "hint": self.hint,
            "metadata": dict(self.metadata),
        }

    def to_text(self) -> str:
        """Return a compact human-readable finding line."""
        location_parts: list[str] = []
        if self.term_id:
            location_parts.append(f"term={self.term_id}")
        if self.surface:
            location_parts.append(f"surface={self.surface!r}")
        if self.scopes:
            location_parts.append(f"scopes={','.join(self.scopes)}")
        location = f" ({'; '.join(location_parts)})" if location_parts else ""
        hint = f" Hint: {self.hint}" if self.hint else ""
        return f"[{self.severity.value}] {self.code.value}: {self.message}{location}.{hint}"


@dataclass(frozen=True, slots=True)
class LexiconLintReport:
    """Lint result for a loaded lexicon document."""

    source_path: str | None
    findings: tuple[LexiconLintFinding, ...] = ()

    @property
    def warning_count(self) -> int:
        """Return the number of warning findings."""
        return sum(1 for finding in self.findings if finding.severity == LexiconLintSeverity.WARNING)

    @property
    def finding_count(self) -> int:
        """Return the total number of findings."""
        return len(self.findings)

    @property
    def passed(self) -> bool:
        """Return whether the lexicon has no lint findings."""
        return not self.findings

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable report payload."""
        return {
            "source_path": self.source_path,
            "passed": self.passed,
            "finding_count": self.finding_count,
            "warning_count": self.warning_count,
            "findings": [finding.to_dict() for finding in self.findings],
        }

    def to_json(self) -> str:
        """Return the report as formatted JSON."""
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    def to_text(self) -> str:
        """Return a human-readable lint summary."""
        label = self.source_path or "<memory>"
        status = "clean" if self.passed else "warnings"
        lines = [f"Lexicon lint: {status} ({self.warning_count} warnings)", f"Lexicon: {label}"]
        lines.extend(finding.to_text() for finding in self.findings)
        return "\n".join(lines)


_BROAD_SURFACE_WORDS = frozenset(
    {
        "account",
        "api",
        "auth",
        "cache",
        "cap",
        "client",
        "config",
        "data",
        "error",
        "event",
        "id",
        "key",
        "limit",
        "log",
        "metric",
        "model",
        "policy",
        "request",
        "response",
        "service",
        "session",
        "state",
        "system",
        "token",
        "url",
        "uri",
        "user",
        "value",
    }
)
_MIN_FRAGMENT_LENGTH = 3
_VERSION_RE = re.compile(r"^version\s*:", re.IGNORECASE)


def lint_lexicon(
    lexicon: Lexicon,
    *,
    source_path: str | Path | None = None,
    has_explicit_version: bool = True,
) -> LexiconLintReport:
    """Run quality checks on a loaded lexicon.

    The function does not mutate the lexicon and never rejects a structurally
    valid document. Use ``lint_lexicon_file`` when missing-version detection is
    required, because loaded lexicons already contain the default schema version.
    """
    findings: list[LexiconLintFinding] = []
    if not has_explicit_version:
        findings.append(
            LexiconLintFinding(
                code=LexiconLintCode.MISSING_VERSION,
                message="lexicon document relies on the default schema version",
                hint="Add version: 1 so reviews and snapshots use an explicit schema contract.",
            )
        )

    surfaces = tuple(_iter_surfaces(lexicon))
    for surface in surfaces:
        findings.extend(_lint_surface(surface))
    findings.extend(_find_normalized_surface_collisions(surfaces))

    return LexiconLintReport(
        source_path=str(source_path) if source_path is not None else None,
        findings=tuple(sorted(findings, key=_finding_sort_key)),
    )


def lint_lexicon_file(path: str | Path, *, document_format: str | None = None) -> LexiconLintReport:
    """Load and lint a JSON or YAML lexicon document."""
    source_path = Path(path)
    lexicon = load_lexicon(source_path, document_format=document_format)
    resolved_format = _resolve_document_format(source_path, document_format=document_format)
    has_explicit_version = _document_has_explicit_version(source_path, document_format=resolved_format)
    return lint_lexicon(lexicon, source_path=source_path, has_explicit_version=has_explicit_version)


@dataclass(frozen=True, slots=True)
class _SurfaceRecord:
    term_id: str
    term_deprecated: bool
    term_has_tools: bool
    surface: str
    kind: str
    scopes: tuple[str, ...]
    case_sensitive: bool
    deprecated: bool

    @property
    def effective_deprecated(self) -> bool:
        return self.term_deprecated or self.deprecated


def _iter_surfaces(lexicon: Lexicon) -> Iterable[_SurfaceRecord]:
    for term in lexicon.terms:
        yield _surface_record_for_term(term)
        for alias in term.aliases:
            yield _surface_record_for_alias(alias, term=term)


def _surface_record_for_term(term: Term) -> _SurfaceRecord:
    return _SurfaceRecord(
        term_id=term.id,
        term_deprecated=term.deprecated,
        term_has_tools=bool(term.tools),
        surface=term.canonical,
        kind="canonical",
        scopes=term.scopes,
        case_sensitive=False,
        deprecated=term.deprecated,
    )


def _surface_record_for_alias(alias: Alias, *, term: Term) -> _SurfaceRecord:
    return _SurfaceRecord(
        term_id=term.id,
        term_deprecated=term.deprecated,
        term_has_tools=bool(term.tools),
        surface=alias.surface,
        kind="alias",
        scopes=alias.scopes or term.scopes,
        case_sensitive=alias.case_sensitive,
        deprecated=alias.deprecated,
    )


def _lint_surface(surface: _SurfaceRecord) -> tuple[LexiconLintFinding, ...]:
    fragments = surface_fragments(surface.surface)
    broad = _is_broad_surface(fragments)
    too_short = _is_short_surface(fragments)
    findings: list[LexiconLintFinding] = []

    if too_short:
        findings.append(
            LexiconLintFinding(
                code=LexiconLintCode.SHORT_SURFACE,
                term_id=surface.term_id,
                surface=surface.surface,
                scopes=surface.scopes,
                message=f"{surface.kind} surface is very short and may match unrelated text",
                hint="Use a more specific surface or restrict it to a narrow scope.",
                metadata={"kind": surface.kind, "fragments": fragments},
            )
        )

    if broad:
        findings.append(
            LexiconLintFinding(
                code=LexiconLintCode.BROAD_SURFACE,
                term_id=surface.term_id,
                surface=surface.surface,
                scopes=surface.scopes,
                message=f"{surface.kind} surface is broad enough to over-trigger in real project text",
                hint="Prefer a domain-specific alias such as 'access token' over a bare word such as 'token'.",
                metadata={"kind": surface.kind, "fragments": fragments},
            )
        )

    if surface.effective_deprecated and (broad or too_short):
        findings.append(
            LexiconLintFinding(
                code=LexiconLintCode.DEPRECATED_BROAD_SURFACE,
                term_id=surface.term_id,
                surface=surface.surface,
                scopes=surface.scopes,
                message="deprecated surface is still broad enough to capture active terminology",
                hint="Keep deprecated aliases narrow so they do not dominate current canonical terms.",
                metadata={"kind": surface.kind, "fragments": fragments},
            )
        )

    if surface.term_has_tools and (broad or too_short):
        findings.append(
            LexiconLintFinding(
                code=LexiconLintCode.TOOL_BROAD_SURFACE,
                term_id=surface.term_id,
                surface=surface.surface,
                scopes=surface.scopes,
                message="tool-routed term uses a broad surface that can affect guard decisions",
                hint="Use explicit tool-facing aliases and avoid bare words on terms with tools.",
                metadata={"kind": surface.kind, "fragments": fragments},
            )
        )

    return tuple(findings)


def _find_normalized_surface_collisions(surfaces: tuple[_SurfaceRecord, ...]) -> tuple[LexiconLintFinding, ...]:
    owners_by_key: dict[tuple[str, tuple[str, ...], bool], list[_SurfaceRecord]] = {}
    for surface in surfaces:
        for search_surface in _runtime_search_surfaces(surface):
            key = (search_surface, tuple(surface.scopes), surface.case_sensitive)
            owners_by_key.setdefault(key, []).append(surface)

    grouped: dict[tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], bool], set[str]] = {}
    for (search_surface, scopes, case_sensitive), owners in owners_by_key.items():
        term_ids = tuple(sorted({owner.term_id for owner in owners}))
        if len(term_ids) < 2:
            continue
        owner_surfaces = tuple(sorted({owner.surface for owner in owners}))
        group_key = (term_ids, owner_surfaces, scopes, case_sensitive)
        grouped.setdefault(group_key, set()).add(search_surface)

    findings: list[LexiconLintFinding] = []
    for (term_ids, owner_surfaces, scopes, case_sensitive), search_surfaces in sorted(grouped.items()):
        primary_search_surface = sorted(search_surfaces)[0]
        findings.append(
            LexiconLintFinding(
                code=LexiconLintCode.NORMALIZED_SURFACE_COLLISION,
                message="runtime-normalized surface maps to multiple terms",
                scopes=scopes,
                hint="Use distinct aliases, narrower scopes, or remove a conflicting generated code-style variant.",
                metadata={
                    "search_surface": primary_search_surface,
                    "search_surfaces": tuple(sorted(search_surfaces)),
                    "case_sensitive": case_sensitive,
                    "term_ids": term_ids,
                    "surfaces": owner_surfaces,
                },
            )
        )
    return tuple(findings)


def _runtime_search_surfaces(surface: _SurfaceRecord) -> tuple[str, ...]:
    values = [surface.surface]
    if not surface.case_sensitive:
        values.extend(code_identifier_variants(surface.surface))
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        search_value = normalize_text_for_matching(value).normalized_text
        if not surface.case_sensitive:
            search_value = search_value.lower()
        if search_value in seen:
            continue
        seen.add(search_value)
        normalized.append(search_value)
    return tuple(normalized)


def _is_broad_surface(fragments: tuple[str, ...]) -> bool:
    return len(fragments) == 1 and fragments[0] in _BROAD_SURFACE_WORDS


def _is_short_surface(fragments: tuple[str, ...]) -> bool:
    return len(fragments) == 1 and len(fragments[0]) < _MIN_FRAGMENT_LENGTH


def _document_has_explicit_version(path: Path, *, document_format: str) -> bool:
    resolved_format = _normalize_document_format(document_format)
    text = path.read_text(encoding="utf-8")
    if resolved_format == "json":
        try:
            payload = json.loads(text)
        except Exception as exc:  # noqa: BLE001 - load_lexicon already provides the user-facing error
            raise AgentLexiconLoadError(f"failed to parse json lexicon from {path}: {exc}") from exc
        return isinstance(payload, Mapping) and "version" in payload
    return _yaml_text_has_top_level_version(text)


def _yaml_text_has_top_level_version(text: str) -> bool:
    content_lines = [
        raw_line
        for raw_line in text.splitlines()
        if raw_line.strip() and not raw_line.strip().startswith("#")
    ]
    if not content_lines:
        return False
    top_indent = min(len(raw_line) - len(raw_line.lstrip(" ")) for raw_line in content_lines)
    for raw_line in content_lines:
        stripped = raw_line.strip()
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if indent == top_indent and _VERSION_RE.match(stripped):
            return True
    return False


def _finding_sort_key(finding: LexiconLintFinding) -> tuple[str, str, str, str]:
    return (
        finding.code.value,
        finding.term_id or "",
        finding.surface or "",
        ",".join(finding.scopes),
    )


__all__ = [
    "LexiconLintCode",
    "LexiconLintFinding",
    "LexiconLintReport",
    "LexiconLintSeverity",
    "lint_lexicon",
    "lint_lexicon_file",
]
