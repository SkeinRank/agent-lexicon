"""Dictionary-as-code layout helpers for Agent Lexicon.

The dictionary-as-code layout is the git-tracked source of truth for a local
Agent Lexicon project. It is intentionally separate from ``.agent-lexicon/``:
that workspace directory stores local SQLite cache and review state, while the
``lexicon/`` layout stores files that can be reviewed in pull requests.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from agent_lexicon.core import AgentLexiconLoadError, load_lexicon
from agent_lexicon.evals import EvalDatasetError, load_eval_queries


DEFAULT_DICTIONARY_DIR = "lexicon"
DEFAULT_LEXICON_FILENAME = "lexicon.yaml"
DEFAULT_QUERIES_FILENAME = "queries.jsonl"
DEFAULT_PROPOSALS_DIR = "proposals"
DEFAULT_SNAPSHOTS_DIR = "snapshots"
DEFAULT_REVIEW_EVENTS_DIR = "review-events"


class DictionaryLayoutError(ValueError):
    """Raised when a dictionary-as-code layout cannot be created or validated."""


@dataclass(frozen=True, slots=True)
class DictionaryLayout:
    """Resolved paths for a git-tracked Agent Lexicon dictionary layout."""

    root_path: str
    layout_path: str
    lexicon_path: str
    queries_path: str
    proposals_path: str
    snapshots_path: str
    review_events_path: str
    readme_path: str

    def to_dict(self) -> dict[str, str]:
        """Return a JSON-serializable layout path mapping."""
        return {
            "root_path": self.root_path,
            "layout_path": self.layout_path,
            "lexicon_path": self.lexicon_path,
            "queries_path": self.queries_path,
            "proposals_path": self.proposals_path,
            "snapshots_path": self.snapshots_path,
            "review_events_path": self.review_events_path,
            "readme_path": self.readme_path,
        }


@dataclass(frozen=True, slots=True)
class DictionaryLayoutSummary:
    """Status for a dictionary-as-code layout on disk."""

    layout: DictionaryLayout
    exists: bool
    lexicon_exists: bool
    lexicon_valid: bool
    queries_exists: bool
    queries_valid: bool
    proposals_dir_exists: bool
    snapshots_dir_exists: bool
    review_events_dir_exists: bool
    proposal_file_count: int = 0
    snapshot_file_count: int = 0
    review_event_file_count: int = 0
    lexicon_error: str | None = None
    queries_error: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def valid(self) -> bool:
        """Return whether the layout exists and contains valid core files."""
        return (
            self.exists
            and self.lexicon_exists
            and self.lexicon_valid
            and self.queries_exists
            and self.queries_valid
            and self.proposals_dir_exists
            and self.snapshots_dir_exists
            and self.review_events_dir_exists
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable status payload."""
        return {
            "layout": self.layout.to_dict(),
            "exists": self.exists,
            "valid": self.valid,
            "lexicon_exists": self.lexicon_exists,
            "lexicon_valid": self.lexicon_valid,
            "queries_exists": self.queries_exists,
            "queries_valid": self.queries_valid,
            "proposals_dir_exists": self.proposals_dir_exists,
            "snapshots_dir_exists": self.snapshots_dir_exists,
            "review_events_dir_exists": self.review_events_dir_exists,
            "proposal_file_count": self.proposal_file_count,
            "snapshot_file_count": self.snapshot_file_count,
            "review_event_file_count": self.review_event_file_count,
            "lexicon_error": self.lexicon_error,
            "queries_error": self.queries_error,
            "metadata": dict(self.metadata),
        }


def dictionary_layout_path(root: str | Path = ".", *, layout_dir: str = DEFAULT_DICTIONARY_DIR) -> DictionaryLayout:
    """Resolve standard dictionary-as-code paths for a project root."""
    root_path = Path(root).expanduser().resolve()
    layout_path = root_path / _clean_layout_dir(layout_dir)
    return DictionaryLayout(
        root_path=str(root_path),
        layout_path=str(layout_path),
        lexicon_path=str(layout_path / DEFAULT_LEXICON_FILENAME),
        queries_path=str(layout_path / DEFAULT_QUERIES_FILENAME),
        proposals_path=str(layout_path / DEFAULT_PROPOSALS_DIR),
        snapshots_path=str(layout_path / DEFAULT_SNAPSHOTS_DIR),
        review_events_path=str(layout_path / DEFAULT_REVIEW_EVENTS_DIR),
        readme_path=str(layout_path / "README.md"),
    )


def init_dictionary_layout(
    root: str | Path = ".",
    *,
    layout_dir: str = DEFAULT_DICTIONARY_DIR,
    force: bool = False,
) -> DictionaryLayoutSummary:
    """Create a git-tracked dictionary-as-code layout.

    Existing files are preserved by default. Pass ``force=True`` to overwrite the
    generated starter files while keeping the directory structure predictable.
    """
    layout = dictionary_layout_path(root, layout_dir=layout_dir)
    layout_path = Path(layout.layout_path)
    if layout_path.exists() and not layout_path.is_dir():
        raise DictionaryLayoutError(f"dictionary layout path is not a directory: {layout_path}")

    layout_path.mkdir(parents=True, exist_ok=True)
    for directory in (layout.proposals_path, layout.snapshots_path, layout.review_events_path):
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        gitkeep = path / ".gitkeep"
        if force or not gitkeep.exists():
            gitkeep.write_text("", encoding="utf-8")

    _write_generated_file(Path(layout.readme_path), _starter_readme(layout_dir=layout_dir), force=force)
    _write_generated_file(Path(layout.lexicon_path), STARTER_LEXICON_YAML, force=force)
    _write_generated_file(Path(layout.queries_path), STARTER_QUERIES_JSONL, force=force)
    return inspect_dictionary_layout(root, layout_dir=layout_dir)


def inspect_dictionary_layout(root: str | Path = ".", *, layout_dir: str = DEFAULT_DICTIONARY_DIR) -> DictionaryLayoutSummary:
    """Inspect the dictionary-as-code layout without mutating files."""
    layout = dictionary_layout_path(root, layout_dir=layout_dir)
    layout_path = Path(layout.layout_path)
    lexicon_path = Path(layout.lexicon_path)
    queries_path = Path(layout.queries_path)
    proposals_path = Path(layout.proposals_path)
    snapshots_path = Path(layout.snapshots_path)
    review_events_path = Path(layout.review_events_path)

    lexicon_valid = False
    lexicon_error: str | None = None
    term_count = 0
    scope_count = 0
    proposal_count = 0
    if lexicon_path.exists():
        try:
            lexicon = load_lexicon(lexicon_path)
            lexicon_valid = True
            term_count = len(lexicon.terms)
            scope_count = len(lexicon.scopes)
            proposal_count = len(lexicon.proposals)
        except AgentLexiconLoadError as exc:
            lexicon_error = str(exc)

    queries_valid = False
    queries_error: str | None = None
    query_count = 0
    if queries_path.exists():
        try:
            queries = load_eval_queries(queries_path)
            queries_valid = True
            query_count = len(queries)
        except EvalDatasetError as exc:
            queries_error = str(exc)

    return DictionaryLayoutSummary(
        layout=layout,
        exists=layout_path.is_dir(),
        lexicon_exists=lexicon_path.is_file(),
        lexicon_valid=lexicon_valid,
        queries_exists=queries_path.is_file(),
        queries_valid=queries_valid,
        proposals_dir_exists=proposals_path.is_dir(),
        snapshots_dir_exists=snapshots_path.is_dir(),
        review_events_dir_exists=review_events_path.is_dir(),
        proposal_file_count=_count_data_files(proposals_path),
        snapshot_file_count=_count_data_files(snapshots_path),
        review_event_file_count=_count_data_files(review_events_path),
        lexicon_error=lexicon_error,
        queries_error=queries_error,
        metadata={
            "scope_count": scope_count,
            "term_count": term_count,
            "proposal_count": proposal_count,
            "query_count": query_count,
        },
    )


def validate_dictionary_layout(root: str | Path = ".", *, layout_dir: str = DEFAULT_DICTIONARY_DIR) -> DictionaryLayoutSummary:
    """Validate the dictionary-as-code layout and raise on invalid state."""
    summary = inspect_dictionary_layout(root, layout_dir=layout_dir)
    errors: list[str] = []
    if not summary.exists:
        errors.append(f"layout directory does not exist: {summary.layout.layout_path}")
    if not summary.lexicon_exists:
        errors.append(f"lexicon file does not exist: {summary.layout.lexicon_path}")
    elif not summary.lexicon_valid:
        errors.append(f"invalid lexicon: {summary.lexicon_error}")
    if not summary.queries_exists:
        errors.append(f"queries file does not exist: {summary.layout.queries_path}")
    elif not summary.queries_valid:
        errors.append(f"invalid queries dataset: {summary.queries_error}")
    if not summary.proposals_dir_exists:
        errors.append(f"proposals directory does not exist: {summary.layout.proposals_path}")
    if not summary.snapshots_dir_exists:
        errors.append(f"snapshots directory does not exist: {summary.layout.snapshots_path}")
    if not summary.review_events_dir_exists:
        errors.append(f"review-events directory does not exist: {summary.layout.review_events_path}")
    if errors:
        raise DictionaryLayoutError("; ".join(errors))
    return summary


def write_dictionary_manifest(summary: DictionaryLayoutSummary, output_path: str | Path) -> Path:
    """Write a JSON manifest for a dictionary-as-code layout summary."""
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(summary.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return destination


def _clean_layout_dir(layout_dir: str) -> str:
    if not isinstance(layout_dir, str):
        raise DictionaryLayoutError("layout_dir must be a string")
    cleaned = layout_dir.strip().strip("/")
    if not cleaned:
        raise DictionaryLayoutError("layout_dir must not be empty")
    if cleaned in {".", ".."} or ".." in Path(cleaned).parts:
        raise DictionaryLayoutError("layout_dir must stay inside the project root")
    return cleaned


def _write_generated_file(path: Path, text: str, *, force: bool) -> None:
    if path.exists() and not force:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _count_data_files(path: Path) -> int:
    if not path.is_dir():
        return 0
    return sum(1 for item in path.iterdir() if item.is_file() and item.name != ".gitkeep")


def _starter_readme(*, layout_dir: str) -> str:
    return f"""# Agent Lexicon Dictionary

This directory is the git-tracked terminology source of truth for Agent Lexicon.

## Files

- `{DEFAULT_LEXICON_FILENAME}` — canonical terms, aliases, scopes, tools, and evidence.
- `{DEFAULT_QUERIES_FILENAME}` — behavior checks for ambiguity, canonicalization, and tool safety.
- `{DEFAULT_PROPOSALS_DIR}/` — reviewable terminology proposal files.
- `{DEFAULT_SNAPSHOTS_DIR}/` — published local snapshots when the team wants to keep them in git.
- `{DEFAULT_REVIEW_EVENTS_DIR}/` — curated review-event exports when they are intentionally committed.

Local SQLite state remains outside git under `.agent-lexicon/`.

## Commands

```bash
agent-lexicon dictionary validate --root . --layout-dir {layout_dir}
agent-lexicon check {layout_dir}/{DEFAULT_LEXICON_FILENAME} {layout_dir}/{DEFAULT_QUERIES_FILENAME}
```
"""


STARTER_LEXICON_YAML = """version: 1
metadata:
  name: Project terminology
  description: Git-tracked Agent Lexicon dictionary.
scopes:
  - id: project
    label: Project
    description: Local project terminology.
terms:
  - id: project.example_term
    canonical: example term
    description: Starter term used to verify the dictionary-as-code layout.
    scopes: [project]
    aliases:
      - surface: example concept
        scopes: [project]
    evidence:
      - source_path: README.md
        start_line: 1
        snippet: Example term for a local dictionary-as-code layout.
        kind: context
proposals: []
"""


STARTER_QUERIES_JSONL = (
    json.dumps(
        {
            "id": "project.example_term",
            "text": "Use the example concept.",
            "scopes": ["project"],
            "expected_status": "resolved",
            "expected_action": "use_terms",
            "expected_term_ids": ["project.example_term"],
            "expected_primary_term_id": "project.example_term",
            "tool_calls": [],
        },
        sort_keys=True,
    )
    + "\n"
)


__all__ = [
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
