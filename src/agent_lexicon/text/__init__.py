"""Text helpers shared by runtime matching and Scout workflows."""

from __future__ import annotations

from .identifiers import code_identifier_variants, normalized_fragment_surface, surface_fragments
from .unicode import (
    UnicodeFindingKind,
    UnicodeNormalizationResult,
    UnicodeTextFinding,
    normalize_text_for_matching,
    unicode_metadata_for_text,
)

__all__ = [
    "UnicodeFindingKind",
    "UnicodeNormalizationResult",
    "UnicodeTextFinding",
    "code_identifier_variants",
    "normalize_text_for_matching",
    "normalized_fragment_surface",
    "surface_fragments",
    "unicode_metadata_for_text",
]
