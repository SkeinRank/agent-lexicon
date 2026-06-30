"""Text and code-identifier normalization helpers.

These helpers are dependency-free and shared by local scout quality scoring and
runtime matching. They make natural language surfaces and code-style identifiers
comparable without putting a tokenizer or embedding model on the hot path.
"""

from __future__ import annotations

import re
from typing import Iterable

_CAMEL_BOUNDARY_RE = re.compile(r"([a-z0-9])([A-Z])")
_ACRONYM_BOUNDARY_RE = re.compile(r"([A-Z]+)([A-Z][a-z])")
_TOKEN_SPLIT_RE = re.compile(r"[^A-Za-z0-9]+")


def surface_fragments(surface: str) -> tuple[str, ...]:
    """Split a natural-language or code-style surface into normalized fragments.

    Examples:
    - ``"access token"`` -> ``("access", "token")``
    - ``"accessToken"`` -> ``("access", "token")``
    - ``"partition_key"`` -> ``("partition", "key")``
    """
    cleaned = str(surface).strip()
    if not cleaned:
        return ()
    camel_split = _CAMEL_BOUNDARY_RE.sub(r"\1 \2", cleaned)
    acronym_split = _ACRONYM_BOUNDARY_RE.sub(r"\1 \2", camel_split)
    raw = _TOKEN_SPLIT_RE.split(acronym_split)
    return tuple(token.casefold() for token in raw if token)


def code_identifier_variants(surface: str) -> tuple[str, ...]:
    """Return code-style variants for a known terminology surface.

    The variants are intended for runtime matching of identifiers such as
    ``accessToken`` or ``access_token`` when the lexicon contains the canonical
    natural-language surface ``access token``. The original surface is not
    included because callers normally already index it directly.
    """
    fragments = surface_fragments(surface)
    if len(fragments) < 2:
        return ()

    variants = [
        _lower_camel(fragments),
        _pascal_case(fragments),
        "_".join(fragments),
        "_".join(fragment.upper() for fragment in fragments),
        "-".join(fragments),
    ]
    return _stable_unique(variant for variant in variants if variant and variant != surface)


def normalized_fragment_surface(surface: str) -> str:
    """Return fragments joined as a normalized natural-language surface."""
    fragments = surface_fragments(surface)
    return " ".join(fragments) if fragments else " ".join(str(surface).strip().casefold().split())


def _lower_camel(fragments: tuple[str, ...]) -> str:
    first, *rest = fragments
    return first + "".join(_title_fragment(fragment) for fragment in rest)


def _pascal_case(fragments: tuple[str, ...]) -> str:
    return "".join(_title_fragment(fragment) for fragment in fragments)


def _title_fragment(fragment: str) -> str:
    if not fragment:
        return ""
    return fragment[:1].upper() + fragment[1:]


def _stable_unique(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return tuple(unique)


__all__ = [
    "code_identifier_variants",
    "normalized_fragment_surface",
    "surface_fragments",
]
