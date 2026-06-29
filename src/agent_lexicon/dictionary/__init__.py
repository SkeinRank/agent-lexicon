"""Dictionary-as-code layout helpers."""

from __future__ import annotations

from .ci import (
    DictionaryCheckError,
    DictionaryCheckItem,
    DictionaryCheckKind,
    DictionaryCheckStatus,
    DictionaryPrCheckReport,
    run_dictionary_pr_checks,
)

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

from .merge import (
    SemanticMergeConflict,
    SemanticMergeError,
    SemanticMergeReport,
    SemanticMergeStatus,
    merge_lexicon_files,
    merge_lexicons,
    write_merged_lexicon_json,
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
    "DictionaryCheckError",
    "DictionaryCheckItem",
    "DictionaryCheckKind",
    "DictionaryCheckStatus",
    "DictionaryPrCheckReport",
    "run_dictionary_pr_checks",
    "SemanticChangeKind",
    "SemanticDiffError",
    "SemanticDiffItem",
    "SemanticDiffReport",
    "SemanticDiffSummary",
    "SemanticObjectKind",
    "diff_lexicon_files",
    "diff_lexicons",
    "SemanticMergeConflict",
    "SemanticMergeError",
    "SemanticMergeReport",
    "SemanticMergeStatus",
    "merge_lexicon_files",
    "merge_lexicons",
    "write_merged_lexicon_json",
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
