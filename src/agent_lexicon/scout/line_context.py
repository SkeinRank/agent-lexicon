"""Deterministic line-context classification for the Scout scan phase.

This module tags each scanned line with a coarse context (prose, comment,
docstring, decorator, string literal, or code) using a small set of
language-family heuristics. It is intentionally not a parser: the goal is a
cheap, dependency-free, deterministic signal that helps candidate scoring
distinguish terminology that lives in human-facing prose from identifiers
that only ever appear in code.

The classifier never affects which lines are scanned; it only annotates them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

__all__ = [
    "LineContext",
    "DocumentContextClassifier",
    "PROSE_CONTEXTS",
    "LINE_CONTEXT_VERSION",
]

LINE_CONTEXT_VERSION = "context-v1"


class LineContext(str, Enum):
    """Coarse context in which a scanned line appears."""

    PROSE = "prose"
    COMMENT = "comment"
    DOCSTRING = "docstring"
    DECORATOR = "decorator"
    STRING = "string"
    CODE = "code"


#: Contexts that represent human-facing prose. Terminology that appears both in
#: prose and in code identifiers is a strong signal of a real domain concept.
PROSE_CONTEXTS = frozenset({LineContext.PROSE, LineContext.COMMENT, LineContext.DOCSTRING})

_PROSE_SUFFIXES = (".md", ".markdown", ".rst", ".txt", ".adoc")
_PYTHON_SUFFIXES = (".py", ".pyi")

_TRIPLE_QUOTE_PATTERN = re.compile(r'("""|\'\'\')')
_DECORATOR_PATTERN = re.compile(r"^@\w[\w.]*")
_LINE_COMMENT_PATTERN = re.compile(r"^(#|//|--\s|<!--)")
_BLOCK_COMMENT_OPEN_PATTERN = re.compile(r"^/\*")
_BLOCK_COMMENT_INNER_PATTERN = re.compile(r"^\*($|[^/])|^\*/")
_STRING_LINE_PATTERN = re.compile(r"""^(?:[frbu]{0,2})?["'](?!"")""", re.IGNORECASE)


def _is_prose_document(relative_path: str) -> bool:
    lowered = relative_path.lower()
    return lowered.endswith(_PROSE_SUFFIXES)


def _is_python_document(relative_path: str) -> bool:
    lowered = relative_path.lower()
    return lowered.endswith(_PYTHON_SUFFIXES)


@dataclass(slots=True)
class DocumentContextClassifier:
    """Classifies stripped lines of one document into a :class:`LineContext`.

    The classifier is stateful only for constructs that span lines (Python
    triple-quoted docstrings and C-style block comments). State transitions are
    driven purely by the line text, so classification is deterministic and
    independent of anything outside the document.
    """

    relative_path: str
    _is_prose: bool = field(init=False)
    _is_python: bool = field(init=False)
    _in_docstring: bool = field(init=False, default=False)
    _in_block_comment: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        self._is_prose = _is_prose_document(self.relative_path)
        self._is_python = _is_python_document(self.relative_path)

    def classify(self, stripped_line: str) -> LineContext:
        """Return the context for one stripped, non-empty line."""
        if self._is_prose:
            return LineContext.PROSE

        if self._in_block_comment:
            if "*/" in stripped_line:
                self._in_block_comment = False
            return LineContext.COMMENT

        if self._is_python and self._in_docstring:
            if _TRIPLE_QUOTE_PATTERN.search(stripped_line):
                self._in_docstring = False
            return LineContext.DOCSTRING

        if self._is_python:
            triple_quotes = _TRIPLE_QUOTE_PATTERN.findall(stripped_line)
            if stripped_line.startswith(('"""', "'''", 'r"""', "r'''")):
                # An odd number of triple quotes opens a docstring block that
                # continues onto following lines; an even number closes on the
                # same line (single-line docstring).
                if len(triple_quotes) % 2 == 1:
                    self._in_docstring = True
                return LineContext.DOCSTRING

        if _LINE_COMMENT_PATTERN.match(stripped_line):
            return LineContext.COMMENT
        if _BLOCK_COMMENT_OPEN_PATTERN.match(stripped_line):
            if "*/" not in stripped_line:
                self._in_block_comment = True
            return LineContext.COMMENT
        if _BLOCK_COMMENT_INNER_PATTERN.match(stripped_line):
            return LineContext.COMMENT
        if _DECORATOR_PATTERN.match(stripped_line):
            return LineContext.DECORATOR
        if _STRING_LINE_PATTERN.match(stripped_line):
            return LineContext.STRING
        return LineContext.CODE
