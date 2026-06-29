"""Dictionary-as-code layout helpers."""

from __future__ import annotations

from .diff import (
    SemanticChangeKind,
    SemanticDiffError,
    SemanticDiffItem,
    SemanticDiffReport,
    SemanticDiffSummary,
    SemanticObjectKind,
    diff_lexicon_files,
    diff_lexicons,
)
from .layout import (
    DEFAULT_DICTIONARY_DIR,
    DEFAULT_LEXICON_FILENAME,
    DEFAULT_PROPOSALS_DIR,
    DEFAULT_QUERIES_FILENAME,
    DEFAULT_REVIEW_EVENTS_DIR,
    DEFAULT_SNAPSHOTS_DIR,
    DictionaryLayout,
    DictionaryLayoutError,
    DictionaryLayoutSummary,
    dictionary_layout_path,
    init_dictionary_layout,
    inspect_dictionary_layout,
    validate_dictionary_layout,
    write_dictionary_manifest,
)

__all__ = [
    "SemanticChangeKind",
    "SemanticDiffError",
    "SemanticDiffItem",
    "SemanticDiffReport",
    "SemanticDiffSummary",
    "SemanticObjectKind",
    "diff_lexicon_files",
    "diff_lexicons",
    "DEFAULT_DICTIONARY_DIR",
    "DEFAULT_LEXICON_FILENAME",
    "DEFAULT_PROPOSALS_DIR",
    "DEFAULT_QUERIES_FILENAME",
    "DEFAULT_REVIEW_EVENTS_DIR",
    "DEFAULT_SNAPSHOTS_DIR",
    "DictionaryLayout",
    "DictionaryLayoutError",
    "DictionaryLayoutSummary",
    "dictionary_layout_path",
    "init_dictionary_layout",
    "inspect_dictionary_layout",
    "validate_dictionary_layout",
    "write_dictionary_manifest",
]
