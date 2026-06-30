"""Semantic merge for Agent Lexicon dictionaries.

The merge operates on validated lexicon objects instead of raw text. It is a
three-way merge: base, ours, and theirs. Safe non-overlapping terminology
changes are merged automatically; competing changes are reported as semantic
conflicts for human review.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from agent_lexicon.core import AgentLexiconLoadError, Lexicon, lexicon_from_dict, load_lexicon
from agent_lexicon.core.files import atomic_write_text

from .diff import SemanticDiffSummary, SemanticObjectKind, diff_lexicons


class SemanticMergeError(ValueError):
    """Raised when a semantic merge cannot be prepared."""


class SemanticMergeStatus(str, Enum):
    """Outcome of a semantic merge."""

    CLEAN = "clean"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class SemanticMergeConflict:
    """One semantic conflict discovered during a three-way merge."""

    object_kind: SemanticObjectKind
    object_id: str
    path: str
    reason: str
    base: Any = None
    ours: Any = None
    theirs: Any = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable conflict payload."""
        return {
            "object_kind": self.object_kind.value,
            "object_id": self.object_id,
            "path": self.path,
            "reason": self.reason,
            "base": _json_ready(self.base),
            "ours": _json_ready(self.ours),
            "theirs": _json_ready(self.theirs),
        }

    def to_text(self) -> str:
        """Return a compact human-readable conflict line."""
        return f"! {self.object_kind.value} {self.object_id}: {self.reason} ({self.path})"


@dataclass(frozen=True, slots=True)
class SemanticMergeReport:
    """Result of a three-way semantic merge."""

    base_label: str
    ours_label: str
    theirs_label: str
    status: SemanticMergeStatus
    conflicts: tuple[SemanticMergeConflict, ...] = ()
    merged_lexicon: Lexicon | None = None
    ours_diff_summary: SemanticDiffSummary = field(default_factory=SemanticDiffSummary)
    theirs_diff_summary: SemanticDiffSummary = field(default_factory=SemanticDiffSummary)
    merged_diff_summary: SemanticDiffSummary = field(default_factory=SemanticDiffSummary)

    @property
    def has_conflicts(self) -> bool:
        """Return whether the merge has semantic conflicts."""
        return bool(self.conflicts)

    @property
    def conflict_count(self) -> int:
        """Return the number of semantic conflicts."""
        return len(self.conflicts)

    def to_dict(self, *, include_merged_lexicon: bool = False) -> dict[str, Any]:
        """Return a JSON-serializable merge report."""
        payload: dict[str, Any] = {
            "base_label": self.base_label,
            "ours_label": self.ours_label,
            "theirs_label": self.theirs_label,
            "status": self.status.value,
            "has_conflicts": self.has_conflicts,
            "conflict_count": self.conflict_count,
            "ours_diff_summary": self.ours_diff_summary.to_dict(),
            "theirs_diff_summary": self.theirs_diff_summary.to_dict(),
            "merged_diff_summary": self.merged_diff_summary.to_dict(),
            "conflicts": [conflict.to_dict() for conflict in self.conflicts],
        }
        if include_merged_lexicon and self.merged_lexicon is not None:
            payload["merged_lexicon"] = self.merged_lexicon.to_dict()
        return payload

    def to_json(self, *, include_merged_lexicon: bool = False) -> str:
        """Return the merge report as stable formatted JSON."""
        return json.dumps(self.to_dict(include_merged_lexicon=include_merged_lexicon), indent=2, sort_keys=True)


def merge_lexicon_files(
    base_path: str | Path,
    ours_path: str | Path,
    theirs_path: str | Path,
) -> SemanticMergeReport:
    """Load three lexicon files and return their semantic merge report."""
    base_file = Path(base_path)
    ours_file = Path(ours_path)
    theirs_file = Path(theirs_path)
    try:
        base = load_lexicon(base_file)
        ours = load_lexicon(ours_file)
        theirs = load_lexicon(theirs_file)
    except AgentLexiconLoadError as exc:
        raise SemanticMergeError(str(exc)) from exc
    return merge_lexicons(
        base,
        ours,
        theirs,
        base_label=str(base_file),
        ours_label=str(ours_file),
        theirs_label=str(theirs_file),
    )


def merge_lexicons(
    base: Lexicon,
    ours: Lexicon,
    theirs: Lexicon,
    *,
    base_label: str = "base",
    ours_label: str = "ours",
    theirs_label: str = "theirs",
) -> SemanticMergeReport:
    """Return a deterministic three-way semantic merge for lexicon objects."""
    ours_diff = diff_lexicons(base, ours, before_label=base_label, after_label=ours_label)
    theirs_diff = diff_lexicons(base, theirs, before_label=base_label, after_label=theirs_label)
    conflicts: list[SemanticMergeConflict] = []

    base_payload = base.to_dict()
    ours_payload = ours.to_dict()
    theirs_payload = theirs.to_dict()

    merged_payload: dict[str, Any] = {"version": "1"}
    merged_payload["metadata"] = _merge_mapping(
        base_payload.get("metadata", {}),
        ours_payload.get("metadata", {}),
        theirs_payload.get("metadata", {}),
        object_kind=SemanticObjectKind.LEXICON,
        object_id="lexicon",
        path="metadata",
        conflicts=conflicts,
    )
    merged_payload["scopes"] = _merge_indexed_list(
        base_payload.get("scopes", []),
        ours_payload.get("scopes", []),
        theirs_payload.get("scopes", []),
        key_fn=lambda item: str(item["id"]),
        object_kind=SemanticObjectKind.SCOPE,
        path_prefix="scopes",
        conflicts=conflicts,
        merge_item_fn=_merge_scope_item,
    )
    merged_payload["terms"] = _merge_indexed_list(
        base_payload.get("terms", []),
        ours_payload.get("terms", []),
        theirs_payload.get("terms", []),
        key_fn=lambda item: str(item["id"]),
        object_kind=SemanticObjectKind.TERM,
        path_prefix="terms",
        conflicts=conflicts,
        merge_item_fn=_merge_term_item,
    )
    merged_payload["proposals"] = _merge_indexed_list(
        base_payload.get("proposals", []),
        ours_payload.get("proposals", []),
        theirs_payload.get("proposals", []),
        key_fn=lambda item: str(item["id"]),
        object_kind=SemanticObjectKind.PROPOSAL,
        path_prefix="proposals",
        conflicts=conflicts,
        merge_item_fn=_merge_proposal_item,
    )

    if conflicts:
        return SemanticMergeReport(
            base_label=base_label,
            ours_label=ours_label,
            theirs_label=theirs_label,
            status=SemanticMergeStatus.CONFLICT,
            conflicts=tuple(_sort_conflicts(conflicts)),
            merged_lexicon=None,
            ours_diff_summary=ours_diff.summary,
            theirs_diff_summary=theirs_diff.summary,
        )

    try:
        merged_lexicon = lexicon_from_dict(merged_payload)
    except AgentLexiconLoadError as exc:
        validation_conflict = SemanticMergeConflict(
            object_kind=SemanticObjectKind.LEXICON,
            object_id="lexicon",
            path="lexicon",
            reason=f"merged lexicon failed validation: {exc}",
            base=base_payload,
            ours=ours_payload,
            theirs=theirs_payload,
        )
        return SemanticMergeReport(
            base_label=base_label,
            ours_label=ours_label,
            theirs_label=theirs_label,
            status=SemanticMergeStatus.CONFLICT,
            conflicts=(validation_conflict,),
            merged_lexicon=None,
            ours_diff_summary=ours_diff.summary,
            theirs_diff_summary=theirs_diff.summary,
        )

    merged_diff = diff_lexicons(base, merged_lexicon, before_label=base_label, after_label="merged")
    return SemanticMergeReport(
        base_label=base_label,
        ours_label=ours_label,
        theirs_label=theirs_label,
        status=SemanticMergeStatus.CLEAN,
        conflicts=(),
        merged_lexicon=merged_lexicon,
        ours_diff_summary=ours_diff.summary,
        theirs_diff_summary=theirs_diff.summary,
        merged_diff_summary=merged_diff.summary,
    )


def write_merged_lexicon_json(report: SemanticMergeReport, output_path: str | Path) -> Path:
    """Write a clean merge result as a lexicon-compatible JSON file."""
    if report.merged_lexicon is None or report.has_conflicts:
        raise SemanticMergeError("cannot write merged lexicon while semantic conflicts are present")
    output = Path(output_path)
    atomic_write_text(
        output,
        json.dumps(report.merged_lexicon.to_dict(), indent=2, sort_keys=True) + "\n",
    )
    return output


MergeItemFn = Callable[[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any], str, list[SemanticMergeConflict]], dict[str, Any]]


def _merge_indexed_list(
    base_items: Sequence[Mapping[str, Any]],
    ours_items: Sequence[Mapping[str, Any]],
    theirs_items: Sequence[Mapping[str, Any]],
    *,
    key_fn: Callable[[Mapping[str, Any]], str],
    object_kind: SemanticObjectKind,
    path_prefix: str,
    conflicts: list[SemanticMergeConflict],
    merge_item_fn: MergeItemFn | None = None,
) -> list[dict[str, Any]]:
    base_by_key = {key_fn(item): dict(item) for item in base_items}
    ours_by_key = {key_fn(item): dict(item) for item in ours_items}
    theirs_by_key = {key_fn(item): dict(item) for item in theirs_items}
    result: list[dict[str, Any]] = []
    for key in _ordered_keys(base_items, ours_items, theirs_items, key_fn=key_fn):
        base = base_by_key.get(key)
        ours = ours_by_key.get(key)
        theirs = theirs_by_key.get(key)
        path = f"{path_prefix}[{key}]"
        merged = _merge_optional_item(
            base,
            ours,
            theirs,
            object_kind=object_kind,
            object_id=key,
            path=path,
            conflicts=conflicts,
            merge_item_fn=merge_item_fn,
        )
        if merged is not None:
            result.append(merged)
    return result


def _merge_optional_item(
    base: Mapping[str, Any] | None,
    ours: Mapping[str, Any] | None,
    theirs: Mapping[str, Any] | None,
    *,
    object_kind: SemanticObjectKind,
    object_id: str,
    path: str,
    conflicts: list[SemanticMergeConflict],
    merge_item_fn: MergeItemFn | None,
) -> dict[str, Any] | None:
    if base is None:
        if ours is None and theirs is None:
            return None
        if ours is None:
            return dict(theirs or {})
        if theirs is None:
            return dict(ours)
        if _same(ours, theirs):
            return dict(ours)
        _append_conflict(conflicts, object_kind, object_id, path, "both sides added different objects", base, ours, theirs)
        return None

    if ours is None and theirs is None:
        return None
    if ours is None:
        if _same(theirs, base):
            return None
        _append_conflict(conflicts, object_kind, object_id, path, "ours removed object while theirs changed it", base, ours, theirs)
        return None
    if theirs is None:
        if _same(ours, base):
            return None
        _append_conflict(conflicts, object_kind, object_id, path, "theirs removed object while ours changed it", base, ours, theirs)
        return None

    ours_changed = not _same(ours, base)
    theirs_changed = not _same(theirs, base)
    if not ours_changed and not theirs_changed:
        return dict(base)
    if ours_changed and not theirs_changed:
        return dict(ours)
    if theirs_changed and not ours_changed:
        return dict(theirs)
    if _same(ours, theirs):
        return dict(ours)
    if merge_item_fn is None:
        _append_conflict(conflicts, object_kind, object_id, path, "both sides changed object differently", base, ours, theirs)
        return None
    return merge_item_fn(base, ours, theirs, path, conflicts)


def _merge_scope_item(
    base: Mapping[str, Any],
    ours: Mapping[str, Any],
    theirs: Mapping[str, Any],
    path: str,
    conflicts: list[SemanticMergeConflict],
) -> dict[str, Any]:
    scope_id = str(base.get("id") or ours.get("id") or theirs.get("id"))
    return {
        "id": scope_id,
        "label": _merge_scalar_field(base, ours, theirs, "label", SemanticObjectKind.SCOPE, scope_id, path, conflicts),
        "description": _merge_scalar_field(base, ours, theirs, "description", SemanticObjectKind.SCOPE, scope_id, path, conflicts),
        "parents": _merge_scalar_sequence(base, ours, theirs, "parents"),
        "metadata": _merge_mapping(
            base.get("metadata", {}),
            ours.get("metadata", {}),
            theirs.get("metadata", {}),
            object_kind=SemanticObjectKind.SCOPE,
            object_id=scope_id,
            path=f"{path}.metadata",
            conflicts=conflicts,
        ),
    }


def _merge_term_item(
    base: Mapping[str, Any],
    ours: Mapping[str, Any],
    theirs: Mapping[str, Any],
    path: str,
    conflicts: list[SemanticMergeConflict],
) -> dict[str, Any]:
    term_id = str(base.get("id") or ours.get("id") or theirs.get("id"))
    return {
        "id": term_id,
        "canonical": _merge_scalar_field(base, ours, theirs, "canonical", SemanticObjectKind.TERM, term_id, path, conflicts),
        "description": _merge_scalar_field(base, ours, theirs, "description", SemanticObjectKind.TERM, term_id, path, conflicts),
        "aliases": _merge_indexed_list(
            base.get("aliases", []),
            ours.get("aliases", []),
            theirs.get("aliases", []),
            key_fn=lambda item: str(item["surface"]),
            object_kind=SemanticObjectKind.ALIAS,
            path_prefix=f"{path}.aliases",
            conflicts=conflicts,
            merge_item_fn=_merge_alias_item,
        ),
        "scopes": _merge_scalar_sequence(base, ours, theirs, "scopes"),
        "tags": _merge_scalar_sequence(base, ours, theirs, "tags"),
        "tools": _merge_scalar_sequence(base, ours, theirs, "tools"),
        "deprecated": _merge_scalar_field(base, ours, theirs, "deprecated", SemanticObjectKind.TERM, term_id, path, conflicts),
        "evidence": _merge_evidence_list(base, ours, theirs, path, term_id, conflicts),
        "metadata": _merge_mapping(
            base.get("metadata", {}),
            ours.get("metadata", {}),
            theirs.get("metadata", {}),
            object_kind=SemanticObjectKind.TERM,
            object_id=term_id,
            path=f"{path}.metadata",
            conflicts=conflicts,
        ),
    }


def _merge_alias_item(
    base: Mapping[str, Any],
    ours: Mapping[str, Any],
    theirs: Mapping[str, Any],
    path: str,
    conflicts: list[SemanticMergeConflict],
) -> dict[str, Any]:
    surface = str(base.get("surface") or ours.get("surface") or theirs.get("surface"))
    alias_id = str(base.get("term_id") or ours.get("term_id") or theirs.get("term_id") or surface)
    return {
        "surface": surface,
        "term_id": _merge_scalar_field(base, ours, theirs, "term_id", SemanticObjectKind.ALIAS, alias_id, path, conflicts),
        "scopes": _merge_scalar_sequence(base, ours, theirs, "scopes"),
        "case_sensitive": _merge_scalar_field(base, ours, theirs, "case_sensitive", SemanticObjectKind.ALIAS, alias_id, path, conflicts),
        "deprecated": _merge_scalar_field(base, ours, theirs, "deprecated", SemanticObjectKind.ALIAS, alias_id, path, conflicts),
        "metadata": _merge_mapping(
            base.get("metadata", {}),
            ours.get("metadata", {}),
            theirs.get("metadata", {}),
            object_kind=SemanticObjectKind.ALIAS,
            object_id=alias_id,
            path=f"{path}.metadata",
            conflicts=conflicts,
        ),
    }


def _merge_proposal_item(
    base: Mapping[str, Any],
    ours: Mapping[str, Any],
    theirs: Mapping[str, Any],
    path: str,
    conflicts: list[SemanticMergeConflict],
) -> dict[str, Any]:
    proposal_id = str(base.get("id") or ours.get("id") or theirs.get("id"))
    return {
        "id": proposal_id,
        "kind": _merge_scalar_field(base, ours, theirs, "kind", SemanticObjectKind.PROPOSAL, proposal_id, path, conflicts),
        "surface": _merge_scalar_field(base, ours, theirs, "surface", SemanticObjectKind.PROPOSAL, proposal_id, path, conflicts),
        "status": _merge_scalar_field(base, ours, theirs, "status", SemanticObjectKind.PROPOSAL, proposal_id, path, conflicts),
        "candidate_term_id": _merge_scalar_field(base, ours, theirs, "candidate_term_id", SemanticObjectKind.PROPOSAL, proposal_id, path, conflicts),
        "target_term_id": _merge_scalar_field(base, ours, theirs, "target_term_id", SemanticObjectKind.PROPOSAL, proposal_id, path, conflicts),
        "confidence": _merge_scalar_field(base, ours, theirs, "confidence", SemanticObjectKind.PROPOSAL, proposal_id, path, conflicts),
        "risk": _merge_scalar_field(base, ours, theirs, "risk", SemanticObjectKind.PROPOSAL, proposal_id, path, conflicts),
        "scopes": _merge_scalar_sequence(base, ours, theirs, "scopes"),
        "evidence": _merge_evidence_list(base, ours, theirs, path, proposal_id, conflicts),
        "rationale": _merge_scalar_field(base, ours, theirs, "rationale", SemanticObjectKind.PROPOSAL, proposal_id, path, conflicts),
        "metadata": _merge_mapping(
            base.get("metadata", {}),
            ours.get("metadata", {}),
            theirs.get("metadata", {}),
            object_kind=SemanticObjectKind.PROPOSAL,
            object_id=proposal_id,
            path=f"{path}.metadata",
            conflicts=conflicts,
        ),
    }


def _merge_evidence_list(
    base: Mapping[str, Any],
    ours: Mapping[str, Any],
    theirs: Mapping[str, Any],
    path: str,
    object_id: str,
    conflicts: list[SemanticMergeConflict],
) -> list[dict[str, Any]]:
    return _merge_indexed_list(
        base.get("evidence", []),
        ours.get("evidence", []),
        theirs.get("evidence", []),
        key_fn=_evidence_key,
        object_kind=SemanticObjectKind.EVIDENCE,
        path_prefix=f"{path}.evidence",
        conflicts=conflicts,
        merge_item_fn=None,
    )


def _merge_scalar_field(
    base: Mapping[str, Any],
    ours: Mapping[str, Any],
    theirs: Mapping[str, Any],
    field: str,
    object_kind: SemanticObjectKind,
    object_id: str,
    path: str,
    conflicts: list[SemanticMergeConflict],
) -> Any:
    base_value = base.get(field)
    ours_value = ours.get(field)
    theirs_value = theirs.get(field)
    ours_changed = ours_value != base_value
    theirs_changed = theirs_value != base_value
    if not ours_changed and not theirs_changed:
        return base_value
    if ours_changed and not theirs_changed:
        return ours_value
    if theirs_changed and not ours_changed:
        return theirs_value
    if ours_value == theirs_value:
        return ours_value
    _append_conflict(
        conflicts,
        object_kind,
        object_id,
        f"{path}.{field}",
        f"both sides changed {field!r} differently",
        base_value,
        ours_value,
        theirs_value,
    )
    return base_value


def _merge_scalar_sequence(base: Mapping[str, Any], ours: Mapping[str, Any], theirs: Mapping[str, Any], field: str) -> list[Any]:
    base_values = list(base.get(field, []))
    ours_values = list(ours.get(field, []))
    theirs_values = list(theirs.get(field, []))
    result: list[Any] = []
    for value in base_values:
        if value in ours_values and value in theirs_values:
            result.append(value)
    for value in [*ours_values, *theirs_values]:
        if value not in base_values and value not in result:
            result.append(value)
    return result


def _merge_mapping(
    base: Mapping[str, Any],
    ours: Mapping[str, Any],
    theirs: Mapping[str, Any],
    *,
    object_kind: SemanticObjectKind,
    object_id: str,
    path: str,
    conflicts: list[SemanticMergeConflict],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in _ordered_mapping_keys(base, ours, theirs):
        base_has = key in base
        ours_has = key in ours
        theirs_has = key in theirs
        base_value = base.get(key)
        ours_value = ours.get(key)
        theirs_value = theirs.get(key)
        key_path = f"{path}.{key}"
        if not base_has:
            if ours_has and theirs_has:
                if ours_value == theirs_value:
                    result[str(key)] = ours_value
                else:
                    _append_conflict(conflicts, object_kind, object_id, key_path, "both sides added metadata key differently", None, ours_value, theirs_value)
            elif ours_has:
                result[str(key)] = ours_value
            elif theirs_has:
                result[str(key)] = theirs_value
            continue
        if not ours_has and not theirs_has:
            continue
        if not ours_has:
            if theirs_value == base_value:
                continue
            _append_conflict(conflicts, object_kind, object_id, key_path, "ours removed metadata key while theirs changed it", base_value, None, theirs_value)
            continue
        if not theirs_has:
            if ours_value == base_value:
                continue
            _append_conflict(conflicts, object_kind, object_id, key_path, "theirs removed metadata key while ours changed it", base_value, ours_value, None)
            continue
        ours_changed = ours_value != base_value
        theirs_changed = theirs_value != base_value
        if not ours_changed and not theirs_changed:
            result[str(key)] = base_value
        elif ours_changed and not theirs_changed:
            result[str(key)] = ours_value
        elif theirs_changed and not ours_changed:
            result[str(key)] = theirs_value
        elif ours_value == theirs_value:
            result[str(key)] = ours_value
        else:
            _append_conflict(conflicts, object_kind, object_id, key_path, "both sides changed metadata key differently", base_value, ours_value, theirs_value)
            result[str(key)] = base_value
    return result


def _ordered_keys(
    base_items: Sequence[Mapping[str, Any]],
    ours_items: Sequence[Mapping[str, Any]],
    theirs_items: Sequence[Mapping[str, Any]],
    *,
    key_fn: Callable[[Mapping[str, Any]], str],
) -> list[str]:
    result: list[str] = []
    for item in [*base_items, *ours_items, *theirs_items]:
        key = key_fn(item)
        if key not in result:
            result.append(key)
    return result


def _ordered_mapping_keys(*mappings: Mapping[str, Any]) -> list[str]:
    result: list[str] = []
    for mapping in mappings:
        for key in mapping.keys():
            str_key = str(key)
            if str_key not in result:
                result.append(str_key)
    return result


def _evidence_key(evidence: Mapping[str, Any]) -> str:
    return "|".join(
        str(evidence.get(name) or "")
        for name in ("source_path", "start_line", "end_line", "kind", "snippet")
    )


def _append_conflict(
    conflicts: list[SemanticMergeConflict],
    object_kind: SemanticObjectKind,
    object_id: str,
    path: str,
    reason: str,
    base: Any,
    ours: Any,
    theirs: Any,
) -> None:
    conflicts.append(
        SemanticMergeConflict(
            object_kind=object_kind,
            object_id=object_id,
            path=path,
            reason=reason,
            base=base,
            ours=ours,
            theirs=theirs,
        )
    )


def _same(left: Mapping[str, Any] | None, right: Mapping[str, Any] | None) -> bool:
    return _json_ready(left) == _json_ready(right)


def _sort_conflicts(conflicts: Iterable[SemanticMergeConflict]) -> list[SemanticMergeConflict]:
    object_order = {item.value: index for index, item in enumerate(SemanticObjectKind)}
    return sorted(
        conflicts,
        key=lambda item: (object_order[item.object_kind.value], item.object_id, item.path, item.reason),
    )


def _json_ready(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _json_ready(value[key]) for key in sorted(value.keys(), key=str)}
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    return value


__all__ = [
    "SemanticMergeConflict",
    "SemanticMergeError",
    "SemanticMergeReport",
    "SemanticMergeStatus",
    "merge_lexicon_files",
    "merge_lexicons",
    "write_merged_lexicon_json",
]
