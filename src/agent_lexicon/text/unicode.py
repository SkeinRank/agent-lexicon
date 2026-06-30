"""Unicode normalization helpers for runtime terminology matching.

Agent Lexicon keeps runtime matching deterministic while still handling common
Unicode lookalikes that appear when text is copied from chats, PDFs, web pages,
or tool output. The policy is intentionally conservative: compatibility forms,
fullwidth characters, invisible separators, and bidi controls are normalized or
removed, while accents and real letters are not folded to ASCII.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import unicodedata


class UnicodeFindingKind(str, Enum):
    """Kinds of Unicode normalization findings."""

    BIDI_CONTROL = "bidi_control"
    COMPATIBILITY = "compatibility"
    NON_ASCII_SPACE = "non_ascii_space"
    ZERO_WIDTH = "zero_width"


_BIDI_CONTROLS = frozenset(
    {
        "\u061c",  # Arabic letter mark
        "\u200e",  # left-to-right mark
        "\u200f",  # right-to-left mark
        "\u202a",  # left-to-right embedding
        "\u202b",  # right-to-left embedding
        "\u202c",  # pop directional formatting
        "\u202d",  # left-to-right override
        "\u202e",  # right-to-left override
        "\u2066",  # left-to-right isolate
        "\u2067",  # right-to-left isolate
        "\u2068",  # first strong isolate
        "\u2069",  # pop directional isolate
    }
)

_ZERO_WIDTH_SEPARATORS = frozenset(
    {
        "\u200b",  # zero width space
        "\u200c",  # zero width non-joiner
        "\u200d",  # zero width joiner
        "\u2060",  # word joiner
        "\ufeff",  # zero width no-break space / BOM
    }
)


@dataclass(frozen=True, slots=True)
class UnicodeTextFinding:
    """A normalization finding discovered in runtime text."""

    kind: UnicodeFindingKind
    start: int
    end: int
    text: str
    replacement: str
    name: str

    @property
    def risk(self) -> str:
        """Return a coarse risk label for guard metadata."""
        return "high" if self.kind == UnicodeFindingKind.BIDI_CONTROL else "low"

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "start": self.start,
            "end": self.end,
            "text": self.text,
            "replacement": self.replacement,
            "name": self.name,
            "risk": self.risk,
        }


@dataclass(frozen=True, slots=True)
class UnicodeNormalizationResult:
    """Result of deterministic Unicode normalization."""

    original_text: str
    normalized_text: str
    index_map: tuple[int, ...]
    findings: tuple[UnicodeTextFinding, ...] = ()

    @property
    def changed(self) -> bool:
        """Return whether the normalized text differs from the input."""
        return self.normalized_text != self.original_text

    @property
    def has_bidi_control(self) -> bool:
        """Return whether bidi-control characters were removed."""
        return any(finding.kind == UnicodeFindingKind.BIDI_CONTROL for finding in self.findings)

    @property
    def has_findings(self) -> bool:
        """Return whether any notable Unicode normalization finding was recorded."""
        return bool(self.findings)

    def original_span(self, start: int, end: int) -> tuple[int, int]:
        """Map a span in normalized text back to the original input text."""
        if start < 0 or end < start:
            raise ValueError("invalid normalized span")
        if not self.index_map or start == end:
            return (0, 0)
        if start >= len(self.index_map) or end > len(self.index_map):
            raise ValueError("normalized span is outside the input text")
        return (self.index_map[start], self.index_map[end - 1] + 1)

    def metadata(self) -> dict[str, object]:
        """Return serializable metadata for decisions and guard results."""
        return {
            "unicode_normalized": self.changed,
            "unicode_findings": [finding.to_dict() for finding in self.findings],
            "unicode_has_bidi_control": self.has_bidi_control,
        }


def normalize_text_for_matching(text: str) -> UnicodeNormalizationResult:
    """Normalize text for deterministic runtime matching.

    Policy:
    - NFKC per character for compatibility/fullwidth/ligature forms.
    - Unicode spaces and zero-width separators become a single plain space.
    - Bidi-control characters are removed and reported as high-risk findings.
    - Accents and non-Latin letters are not folded to ASCII.
    """
    if not isinstance(text, str):
        raise TypeError("text must be a string")

    raw_chars: list[str] = []
    raw_map: list[int] = []
    findings: list[UnicodeTextFinding] = []

    for index, char in enumerate(text):
        if char in _BIDI_CONTROLS:
            findings.append(_finding(UnicodeFindingKind.BIDI_CONTROL, index, char, ""))
            continue

        if char in _ZERO_WIDTH_SEPARATORS:
            findings.append(_finding(UnicodeFindingKind.ZERO_WIDTH, index, char, " "))
            raw_chars.append(" ")
            raw_map.append(index)
            continue

        if char.isspace() and char != " ":
            findings.append(_finding(UnicodeFindingKind.NON_ASCII_SPACE, index, char, " "))
            raw_chars.append(" ")
            raw_map.append(index)
            continue

        normalized = unicodedata.normalize("NFKC", char)
        if normalized != char:
            findings.append(_finding(UnicodeFindingKind.COMPATIBILITY, index, char, normalized))
        for normalized_char in normalized:
            raw_chars.append(normalized_char)
            raw_map.append(index)

    collapsed_chars: list[str] = []
    collapsed_map: list[int] = []
    previous_was_space = False
    for char, source_index in zip(raw_chars, raw_map):
        if char.isspace():
            if previous_was_space:
                continue
            collapsed_chars.append(" ")
            collapsed_map.append(source_index)
            previous_was_space = True
            continue
        collapsed_chars.append(char)
        collapsed_map.append(source_index)
        previous_was_space = False

    return UnicodeNormalizationResult(
        original_text=text,
        normalized_text="".join(collapsed_chars),
        index_map=tuple(collapsed_map),
        findings=tuple(findings),
    )


def unicode_metadata_for_text(text: str) -> dict[str, object]:
    """Return runtime Unicode normalization metadata for text."""
    return normalize_text_for_matching(text).metadata()


def _finding(kind: UnicodeFindingKind, index: int, text: str, replacement: str) -> UnicodeTextFinding:
    name = unicodedata.name(text, "UNKNOWN") if text else "UNKNOWN"
    return UnicodeTextFinding(
        kind=kind,
        start=index,
        end=index + 1,
        text=text,
        replacement=replacement,
        name=name,
    )


__all__ = [
    "UnicodeFindingKind",
    "UnicodeNormalizationResult",
    "UnicodeTextFinding",
    "normalize_text_for_matching",
    "unicode_metadata_for_text",
]
