"""Deterministic repository-relative glob matching.

Python's :mod:`fnmatch` treats ``**`` like an ordinary ``*``. In particular,
``**/*.py`` does not match a root-level ``main.py`` because the slash remains
mandatory. Repository configuration normally expects globstar directories to
be optional, so this module adds that one compatibility rule while preserving
the package's existing fnmatch-style behavior.
"""

from __future__ import annotations

import fnmatch
from functools import lru_cache
from pathlib import Path


def normalize_repo_path(path: str | Path) -> str:
    """Return a normalized repository-relative path using forward slashes."""
    normalized = str(path).replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.lstrip("/")


def repo_path_matches(path: str | Path, pattern: str) -> bool:
    """Match a repository path with zero-directory globstar semantics.

    The matcher keeps the historical ``fnmatch`` behavior used by Agent
    Lexicon, but also evaluates variants where each ``**/`` segment consumes
    zero directories. Consequently, ``**/*.py`` matches both ``main.py`` and
    ``src/main.py``, and ``src/**/*.py`` matches both ``src/main.py`` and
    ``src/domain/main.py``.
    """
    normalized_path = normalize_repo_path(path)
    normalized_pattern = str(pattern).replace("\\", "/").strip()
    while normalized_pattern.startswith("./"):
        normalized_pattern = normalized_pattern[2:]
    normalized_pattern = normalized_pattern.lstrip("/")
    if not normalized_path or not normalized_pattern:
        return False
    return any(
        fnmatch.fnmatchcase(normalized_path, candidate)
        for candidate in _globstar_zero_directory_variants(normalized_pattern)
    )


@lru_cache(maxsize=1024)
def _globstar_zero_directory_variants(pattern: str) -> tuple[str, ...]:
    """Return ``pattern`` plus variants where ``**/`` consumes no directory."""
    variants = {pattern}
    pending = [pattern]
    while pending:
        current = pending.pop()
        start = 0
        while True:
            index = current.find("**/", start)
            if index < 0:
                break
            candidate = current[:index] + current[index + 3 :]
            if candidate not in variants:
                variants.add(candidate)
                pending.append(candidate)
            start = index + 1
    return tuple(sorted(variants))


__all__ = ["normalize_repo_path", "repo_path_matches"]
