"""Surface matching for Agent Lexicon terms and aliases.

The matcher uses a small dependency-free trie with Aho-Corasick failure links.
It is designed for local runtime checks where agents need fast lookup of known
canonical terms, aliases, code-style names, and deprecated surfaces in text.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from agent_lexicon.text import code_identifier_variants, normalize_text_for_matching

from .models import Alias, Lexicon, Term


class SurfaceKind(str, Enum):
    """Where a matched surface came from."""

    CANONICAL = "canonical"
    ALIAS = "alias"


@dataclass(frozen=True, slots=True)
class SurfaceEntry:
    """A searchable canonical surface or alias surface."""

    surface: str
    term_id: str
    kind: SurfaceKind
    scopes: tuple[str, ...] = ()
    case_sensitive: bool = False
    deprecated: bool = False
    match_surface: str | None = None
    search_surface_value: str | None = None

    @property
    def search_surface(self) -> str:
        """Return the normalized surface value used by the trie."""
        if self.search_surface_value is not None:
            return self.search_surface_value
        return _search_surface_for(self.match_surface or self.surface, case_sensitive=self.case_sensitive)

    @property
    def is_code_variant(self) -> bool:
        """Return whether this entry searches a generated code identifier variant."""
        return self.match_surface is not None and self.match_surface != self.surface


@dataclass(frozen=True, slots=True)
class SurfaceMatch:
    """A surface occurrence found in text."""

    term_id: str
    surface: str
    matched_text: str
    start: int
    end: int
    kind: SurfaceKind
    scopes: tuple[str, ...] = ()
    case_sensitive: bool = False
    deprecated: bool = False

    @property
    def length(self) -> int:
        """Return the number of characters covered by the match."""
        return self.end - self.start

    def to_dict(self) -> dict[str, object]:
        return {
            "term_id": self.term_id,
            "surface": self.surface,
            "matched_text": self.matched_text,
            "start": self.start,
            "end": self.end,
            "kind": self.kind.value,
            "scopes": list(self.scopes),
            "case_sensitive": self.case_sensitive,
            "deprecated": self.deprecated,
        }


@dataclass(slots=True)
class _TrieNode:
    children: dict[str, int]
    fail: int
    output_indexes: list[int]


class SurfaceMatcher:
    """Match Agent Lexicon surfaces against text.

    The matcher has separate automatons for case-insensitive and case-sensitive
    entries. It returns every valid surface occurrence by default and can also
    collapse overlapping matches to longest non-overlapping spans.
    """

    def __init__(self, entries: Iterable[SurfaceEntry]) -> None:
        cleaned_entries = tuple(_dedupe_entries(entries))
        self.entries = cleaned_entries
        self._case_insensitive_entries = tuple(entry for entry in cleaned_entries if not entry.case_sensitive)
        self._case_sensitive_entries = tuple(entry for entry in cleaned_entries if entry.case_sensitive)
        self._case_insensitive_nodes = _build_automaton(self._case_insensitive_entries)
        self._case_sensitive_nodes = _build_automaton(self._case_sensitive_entries)

    @classmethod
    def from_lexicon(cls, lexicon: Lexicon, *, include_deprecated: bool = True) -> "SurfaceMatcher":
        """Build a matcher from canonical terms and aliases in a lexicon."""
        entries: list[SurfaceEntry] = []
        for term in lexicon.terms:
            entries.extend(
                _entries_for_surface(
                    surface=term.canonical,
                    term_id=term.id,
                    kind=SurfaceKind.CANONICAL,
                    scopes=term.scopes,
                    deprecated=term.deprecated,
                    excluded_search_surfaces=_alias_search_surfaces(term.aliases),
                )
            )
            for alias in term.aliases:
                if include_deprecated or not alias.deprecated:
                    entries.extend(_entries_from_alias(alias, fallback_scopes=term.scopes))
        return cls(entries)

    def match(
        self,
        text: str,
        *,
        scopes: Iterable[str] | None = None,
        include_deprecated: bool = True,
        longest_only: bool = False,
    ) -> tuple[SurfaceMatch, ...]:
        """Return surface matches found in text.

        Scope filtering is inclusive: entries without scopes are global, and
        entries with scopes match when at least one requested scope overlaps.
        """
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        requested_scopes = _normalize_scopes(scopes)
        normalized_input = normalize_text_for_matching(text)
        normalized_text = normalized_input.normalized_text

        matches: list[SurfaceMatch] = []
        if self._case_insensitive_entries:
            matches.extend(
                _scan(
                    original_text=text,
                    normalized_text=normalized_text,
                    search_text=normalized_text.lower(),
                    normalization=normalized_input,
                    nodes=self._case_insensitive_nodes,
                    entries=self._case_insensitive_entries,
                    requested_scopes=requested_scopes,
                    include_deprecated=include_deprecated,
                )
            )
        if self._case_sensitive_entries:
            matches.extend(
                _scan(
                    original_text=text,
                    normalized_text=normalized_text,
                    search_text=normalized_text,
                    normalization=normalized_input,
                    nodes=self._case_sensitive_nodes,
                    entries=self._case_sensitive_entries,
                    requested_scopes=requested_scopes,
                    include_deprecated=include_deprecated,
                )
            )

        ordered = tuple(sorted(matches, key=lambda match: (match.start, -match.length, match.term_id, match.surface)))
        if longest_only:
            return _longest_non_overlapping(ordered)
        return ordered


def build_surface_matcher(lexicon: Lexicon, *, include_deprecated: bool = True) -> SurfaceMatcher:
    """Build a :class:`SurfaceMatcher` from a lexicon."""
    return SurfaceMatcher.from_lexicon(lexicon, include_deprecated=include_deprecated)


def find_surface_matches(
    lexicon: Lexicon,
    text: str,
    *,
    scopes: Iterable[str] | None = None,
    include_deprecated: bool = True,
    longest_only: bool = False,
) -> tuple[SurfaceMatch, ...]:
    """Convenience helper that builds a matcher and scans text."""
    return build_surface_matcher(lexicon, include_deprecated=include_deprecated).match(
        text,
        scopes=scopes,
        include_deprecated=include_deprecated,
        longest_only=longest_only,
    )


def _entries_from_alias(alias: Alias, *, fallback_scopes: tuple[str, ...]) -> tuple[SurfaceEntry, ...]:
    return _entries_for_surface(
        surface=alias.surface,
        term_id=alias.term_id,
        kind=SurfaceKind.ALIAS,
        scopes=alias.scopes or fallback_scopes,
        case_sensitive=alias.case_sensitive,
        deprecated=alias.deprecated,
    )


def _entries_for_surface(
    *,
    surface: str,
    term_id: str,
    kind: SurfaceKind,
    scopes: tuple[str, ...],
    case_sensitive: bool = False,
    deprecated: bool = False,
    excluded_search_surfaces: frozenset[str] | None = None,
) -> tuple[SurfaceEntry, ...]:
    entries = [
        SurfaceEntry(
            surface=surface,
            term_id=term_id,
            kind=kind,
            scopes=scopes,
            case_sensitive=case_sensitive,
            deprecated=deprecated,
            search_surface_value=_search_surface_for(surface, case_sensitive=case_sensitive),
        )
    ]
    if not case_sensitive:
        reserved = excluded_search_surfaces or frozenset()
        entries.extend(
            SurfaceEntry(
                surface=surface,
                term_id=term_id,
                kind=kind,
                scopes=scopes,
                case_sensitive=False,
                deprecated=deprecated,
                match_surface=variant,
                search_surface_value=_search_surface_for(variant, case_sensitive=False),
            )
            for variant in code_identifier_variants(surface)
            if variant.lower() not in reserved
        )
    return tuple(entries)


def _search_surface_for(surface: str, *, case_sensitive: bool) -> str:
    normalized = normalize_text_for_matching(surface).normalized_text
    return normalized if case_sensitive else normalized.lower()


def _alias_search_surfaces(aliases: tuple[Alias, ...]) -> frozenset[str]:
    surfaces: set[str] = set()
    for alias in aliases:
        surfaces.add(alias.surface)
        surfaces.add(alias.surface.lower())
    return frozenset(surfaces)


def _dedupe_entries(entries: Iterable[SurfaceEntry]) -> tuple[SurfaceEntry, ...]:
    seen: set[tuple[str, str, str, tuple[str, ...], bool, bool]] = set()
    deduped: list[SurfaceEntry] = []
    for entry in entries:
        key = (
            entry.search_surface,
            entry.term_id,
            entry.kind.value,
            tuple(entry.scopes),
            entry.case_sensitive,
            entry.deprecated,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)
    return tuple(deduped)


def _build_automaton(entries: tuple[SurfaceEntry, ...]) -> list[_TrieNode]:
    nodes = [_TrieNode(children={}, fail=0, output_indexes=[])]
    for entry_index, entry in enumerate(entries):
        node_index = 0
        for char in entry.search_surface:
            node = nodes[node_index]
            if char not in node.children:
                node.children[char] = len(nodes)
                nodes.append(_TrieNode(children={}, fail=0, output_indexes=[]))
            node_index = node.children[char]
        nodes[node_index].output_indexes.append(entry_index)

    queue: deque[int] = deque()
    for child_index in nodes[0].children.values():
        nodes[child_index].fail = 0
        queue.append(child_index)

    while queue:
        current_index = queue.popleft()
        current_node = nodes[current_index]
        for char, child_index in current_node.children.items():
            fallback_index = current_node.fail
            while fallback_index and char not in nodes[fallback_index].children:
                fallback_index = nodes[fallback_index].fail
            nodes[child_index].fail = nodes[fallback_index].children.get(char, 0)
            nodes[child_index].output_indexes.extend(nodes[nodes[child_index].fail].output_indexes)
            queue.append(child_index)

    return nodes


def _scan(
    *,
    original_text: str,
    normalized_text: str,
    search_text: str,
    normalization,
    nodes: list[_TrieNode],
    entries: tuple[SurfaceEntry, ...],
    requested_scopes: frozenset[str] | None,
    include_deprecated: bool,
) -> list[SurfaceMatch]:
    matches: list[SurfaceMatch] = []
    node_index = 0
    for index, char in enumerate(search_text):
        while node_index and char not in nodes[node_index].children:
            node_index = nodes[node_index].fail
        node_index = nodes[node_index].children.get(char, 0)
        for entry_index in nodes[node_index].output_indexes:
            entry = entries[entry_index]
            if entry.deprecated and not include_deprecated:
                continue
            if not _scope_matches(entry.scopes, requested_scopes):
                continue
            normalized_start = index - len(entry.search_surface) + 1
            normalized_end = index + 1
            if normalized_start < 0 or normalized_end > len(normalized_text):
                continue
            if not _has_token_boundaries(normalized_text, start=normalized_start, end=normalized_end):
                continue
            start, end = normalization.original_span(normalized_start, normalized_end)
            matched_text = original_text[start:end]
            matches.append(
                SurfaceMatch(
                    term_id=entry.term_id,
                    surface=entry.surface,
                    matched_text=matched_text,
                    start=start,
                    end=end,
                    kind=entry.kind,
                    scopes=entry.scopes,
                    case_sensitive=entry.case_sensitive,
                    deprecated=entry.deprecated,
                )
            )
    return matches


def _normalize_scopes(scopes: Iterable[str] | None) -> frozenset[str] | None:
    if scopes is None:
        return None
    cleaned = frozenset(scope.strip() for scope in scopes if scope.strip())
    return cleaned or None


def _scope_matches(entry_scopes: tuple[str, ...], requested_scopes: frozenset[str] | None) -> bool:
    if requested_scopes is None:
        return True
    if not entry_scopes:
        return True
    return bool(set(entry_scopes) & set(requested_scopes))


def _has_token_boundaries(text: str, *, start: int, end: int) -> bool:
    before = text[start - 1] if start > 0 else ""
    after = text[end] if end < len(text) else ""
    first = text[start] if start < len(text) else ""
    last = text[end - 1] if end > start else ""

    if first and _is_word_char(first) and before and _is_word_char(before):
        return False
    if last and _is_word_char(last) and after and _is_word_char(after):
        return False
    return True


def _is_word_char(char: str) -> bool:
    return char.isalnum() or char == "_"


def _longest_non_overlapping(matches: tuple[SurfaceMatch, ...]) -> tuple[SurfaceMatch, ...]:
    return _select_long_non_overlapping(matches, preserve_same_span=False)


def _select_long_non_overlapping(
    matches: tuple[SurfaceMatch, ...],
    *,
    preserve_same_span: bool,
) -> tuple[SurfaceMatch, ...]:
    """Keep longest non-overlapping spans without quadratic overlap checks."""
    if not matches:
        return ()

    candidates = sorted(matches, key=lambda match: (-match.length, match.start, match.term_id, match.surface))
    accepted: list[SurfaceMatch] = []
    accepted_spans: set[tuple[int, int]] = set()
    max_end = max(match.end for match in candidates)
    occupied = bytearray(max_end)

    for match in candidates:
        span = (match.start, match.end)
        if preserve_same_span and span in accepted_spans:
            accepted.append(match)
            continue
        if occupied.find(1, match.start, match.end) != -1:
            continue
        accepted.append(match)
        accepted_spans.add(span)
        occupied[match.start:match.end] = b"\x01" * match.length

    return tuple(sorted(accepted, key=lambda match: (match.start, -match.length, match.term_id, match.surface)))


__all__ = [
    "SurfaceEntry",
    "SurfaceKind",
    "SurfaceMatch",
    "SurfaceMatcher",
    "build_surface_matcher",
    "find_surface_matches",
]
