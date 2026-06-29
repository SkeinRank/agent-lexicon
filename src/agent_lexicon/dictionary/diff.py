"""Semantic diff for Agent Lexicon dictionaries.

The diff compares validated lexicon objects rather than raw file lines. This
keeps git workflows focused on terminology changes: added terms, removed
aliases, changed scopes, tool mappings, evidence, and proposals.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from agent_lexicon.core import AgentLexiconLoadError, Alias, Lexicon, ProposalCandidate, Scope, Term, load_lexicon


class SemanticDiffError(ValueError):
    """Raised when a semantic diff cannot be produced."""


class SemanticChangeKind(str, Enum):
    """High-level semantic change category."""

    ADDED = "added"
    REMOVED = "removed"
    CHANGED = "changed"


class SemanticObjectKind(str, Enum):
    """Lexicon object type involved in a semantic change."""

    LEXICON = "lexicon"
    SCOPE = "scope"
    TERM = "term"
    ALIAS = "alias"
    TOOL = "tool"
    EVIDENCE = "evidence"
    PROPOSAL = "proposal"


@dataclass(frozen=True, slots=True)
class SemanticDiffItem:
    """One semantic change between two lexicon versions."""

    change: SemanticChangeKind
    object_kind: SemanticObjectKind
    object_id: str
    path: str
    field: str | None = None
    before: Any = None
    after: Any = None
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable change payload."""
        return {
            "change": self.change.value,
            "object_kind": self.object_kind.value,
            "object_id": self.object_id,
            "path": self.path,
            "field": self.field,
            "before": _json_ready(self.before),
            "after": _json_ready(self.after),
            "detail": self.detail,
        }

    def to_text(self) -> str:
        """Return a compact human-readable change line."""
        marker = {
            SemanticChangeKind.ADDED: "+",
            SemanticChangeKind.REMOVED: "-",
            SemanticChangeKind.CHANGED: "~",
        }[self.change]
        suffix = f".{self.field}" if self.field else ""
        if self.detail:
            return f"{marker} {self.object_kind.value} {self.object_id}{suffix}: {self.detail}"
        if self.change == SemanticChangeKind.CHANGED:
            return f"{marker} {self.object_kind.value} {self.object_id}{suffix}: {self.before!r} -> {self.after!r}"
        return f"{marker} {self.object_kind.value} {self.object_id}{suffix}"


@dataclass(frozen=True, slots=True)
class SemanticDiffSummary:
    """Aggregated counts for a semantic diff report."""

    added: int = 0
    removed: int = 0
    changed: int = 0
    by_object_kind: Mapping[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        """Return the total number of semantic changes."""
        return self.added + self.removed + self.changed

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable summary."""
        return {
            "total": self.total,
            "added": self.added,
            "removed": self.removed,
            "changed": self.changed,
            "by_object_kind": dict(self.by_object_kind),
        }


@dataclass(frozen=True, slots=True)
class SemanticDiffReport:
    """Semantic diff report for two lexicon versions."""

    before_label: str
    after_label: str
    changes: tuple[SemanticDiffItem, ...] = ()

    @property
    def has_changes(self) -> bool:
        """Return whether the report contains at least one semantic change."""
        return bool(self.changes)

    @property
    def summary(self) -> SemanticDiffSummary:
        """Return aggregate change counts."""
        added = sum(1 for change in self.changes if change.change == SemanticChangeKind.ADDED)
        removed = sum(1 for change in self.changes if change.change == SemanticChangeKind.REMOVED)
        changed = sum(1 for change in self.changes if change.change == SemanticChangeKind.CHANGED)
        by_object_kind: dict[str, int] = {}
        for change in self.changes:
            by_object_kind[change.object_kind.value] = by_object_kind.get(change.object_kind.value, 0) + 1
        return SemanticDiffSummary(added=added, removed=removed, changed=changed, by_object_kind=by_object_kind)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable report."""
        return {
            "before_label": self.before_label,
            "after_label": self.after_label,
            "has_changes": self.has_changes,
            "summary": self.summary.to_dict(),
            "changes": [change.to_dict() for change in self.changes],
        }

    def to_json(self) -> str:
        """Return the report as stable formatted JSON."""
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)


def diff_lexicon_files(before_path: str | Path, after_path: str | Path) -> SemanticDiffReport:
    """Load two lexicon files and return their semantic diff."""
    before_file = Path(before_path)
    after_file = Path(after_path)
    try:
        before = load_lexicon(before_file)
        after = load_lexicon(after_file)
    except AgentLexiconLoadError as exc:
        raise SemanticDiffError(str(exc)) from exc
    return diff_lexicons(before, after, before_label=str(before_file), after_label=str(after_file))


def diff_lexicons(
    before: Lexicon,
    after: Lexicon,
    *,
    before_label: str = "before",
    after_label: str = "after",
) -> SemanticDiffReport:
    """Return a deterministic semantic diff between two lexicon objects."""
    changes: list[SemanticDiffItem] = []
    _diff_mapping_field(
        changes,
        object_kind=SemanticObjectKind.LEXICON,
        object_id="lexicon",
        path="metadata",
        field="metadata",
        before=before.metadata,
        after=after.metadata,
    )
    _diff_scopes(changes, before.scopes, after.scopes)
    _diff_terms(changes, before.terms, after.terms)
    _diff_proposals(changes, before.proposals, after.proposals)
    return SemanticDiffReport(
        before_label=before_label,
        after_label=after_label,
        changes=tuple(_sort_changes(changes)),
    )


def _diff_scopes(changes: list[SemanticDiffItem], before_scopes: Sequence[Scope], after_scopes: Sequence[Scope]) -> None:
    before_by_id = {scope.id: scope for scope in before_scopes}
    after_by_id = {scope.id: scope for scope in after_scopes}
    for scope_id in sorted(before_by_id.keys() - after_by_id.keys()):
        _append_removed(changes, SemanticObjectKind.SCOPE, scope_id, f"scopes[{scope_id}]", before_by_id[scope_id].to_dict())
    for scope_id in sorted(after_by_id.keys() - before_by_id.keys()):
        _append_added(changes, SemanticObjectKind.SCOPE, scope_id, f"scopes[{scope_id}]", after_by_id[scope_id].to_dict())
    for scope_id in sorted(before_by_id.keys() & after_by_id.keys()):
        before = before_by_id[scope_id]
        after = after_by_id[scope_id]
        path = f"scopes[{scope_id}]"
        _diff_scalar_field(changes, SemanticObjectKind.SCOPE, scope_id, path, "label", before.label, after.label)
        _diff_scalar_field(changes, SemanticObjectKind.SCOPE, scope_id, path, "description", before.description, after.description)
        _diff_sequence_field(changes, SemanticObjectKind.SCOPE, scope_id, path, "parents", before.parents, after.parents)
        _diff_mapping_field(changes, SemanticObjectKind.SCOPE, scope_id, path, "metadata", before.metadata, after.metadata)


def _diff_terms(changes: list[SemanticDiffItem], before_terms: Sequence[Term], after_terms: Sequence[Term]) -> None:
    before_by_id = {term.id: term for term in before_terms}
    after_by_id = {term.id: term for term in after_terms}
    for term_id in sorted(before_by_id.keys() - after_by_id.keys()):
        _append_removed(changes, SemanticObjectKind.TERM, term_id, f"terms[{term_id}]", before_by_id[term_id].to_dict())
    for term_id in sorted(after_by_id.keys() - before_by_id.keys()):
        _append_added(changes, SemanticObjectKind.TERM, term_id, f"terms[{term_id}]", after_by_id[term_id].to_dict())
    for term_id in sorted(before_by_id.keys() & after_by_id.keys()):
        before = before_by_id[term_id]
        after = after_by_id[term_id]
        path = f"terms[{term_id}]"
        _diff_scalar_field(changes, SemanticObjectKind.TERM, term_id, path, "canonical", before.canonical, after.canonical)
        _diff_scalar_field(changes, SemanticObjectKind.TERM, term_id, path, "description", before.description, after.description)
        _diff_sequence_field(changes, SemanticObjectKind.TERM, term_id, path, "scopes", before.scopes, after.scopes)
        _diff_sequence_field(changes, SemanticObjectKind.TERM, term_id, path, "tags", before.tags, after.tags)
        _diff_scalar_field(changes, SemanticObjectKind.TERM, term_id, path, "deprecated", before.deprecated, after.deprecated)
        _diff_mapping_field(changes, SemanticObjectKind.TERM, term_id, path, "metadata", before.metadata, after.metadata)
        _diff_tools(changes, term_id, before.tools, after.tools)
        _diff_aliases(changes, term_id, before.aliases, after.aliases)
        _diff_evidence(changes, term_id, before.evidence, after.evidence, path_prefix=path)


def _diff_aliases(changes: list[SemanticDiffItem], term_id: str, before_aliases: Sequence[Alias], after_aliases: Sequence[Alias]) -> None:
    before_by_id = {_alias_key(alias): alias for alias in before_aliases}
    after_by_id = {_alias_key(alias): alias for alias in after_aliases}
    for alias_id in sorted(before_by_id.keys() - after_by_id.keys()):
        path = f"terms[{term_id}].aliases[{alias_id}]"
        _append_removed(changes, SemanticObjectKind.ALIAS, f"{term_id}:{alias_id}", path, before_by_id[alias_id].to_dict())
    for alias_id in sorted(after_by_id.keys() - before_by_id.keys()):
        path = f"terms[{term_id}].aliases[{alias_id}]"
        _append_added(changes, SemanticObjectKind.ALIAS, f"{term_id}:{alias_id}", path, after_by_id[alias_id].to_dict())
    for alias_id in sorted(before_by_id.keys() & after_by_id.keys()):
        before = before_by_id[alias_id]
        after = after_by_id[alias_id]
        object_id = f"{term_id}:{alias_id}"
        path = f"terms[{term_id}].aliases[{alias_id}]"
        _diff_sequence_field(changes, SemanticObjectKind.ALIAS, object_id, path, "scopes", before.scopes, after.scopes)
        _diff_scalar_field(changes, SemanticObjectKind.ALIAS, object_id, path, "case_sensitive", before.case_sensitive, after.case_sensitive)
        _diff_scalar_field(changes, SemanticObjectKind.ALIAS, object_id, path, "deprecated", before.deprecated, after.deprecated)
        _diff_mapping_field(changes, SemanticObjectKind.ALIAS, object_id, path, "metadata", before.metadata, after.metadata)


def _diff_tools(changes: list[SemanticDiffItem], term_id: str, before_tools: Sequence[str], after_tools: Sequence[str]) -> None:
    before_set = set(before_tools)
    after_set = set(after_tools)
    for tool in sorted(before_set - after_set):
        _append_removed(
            changes,
            SemanticObjectKind.TOOL,
            f"{term_id}:{tool}",
            f"terms[{term_id}].tools[{tool}]",
            tool,
        )
    for tool in sorted(after_set - before_set):
        _append_added(
            changes,
            SemanticObjectKind.TOOL,
            f"{term_id}:{tool}",
            f"terms[{term_id}].tools[{tool}]",
            tool,
        )


def _diff_evidence(
    changes: list[SemanticDiffItem],
    term_id: str,
    before_evidence: Sequence[Any],
    after_evidence: Sequence[Any],
    *,
    path_prefix: str,
) -> None:
    before_by_id = {_evidence_key(item): item.to_dict() for item in before_evidence}
    after_by_id = {_evidence_key(item): item.to_dict() for item in after_evidence}
    for evidence_id in sorted(before_by_id.keys() - after_by_id.keys()):
        _append_removed(
            changes,
            SemanticObjectKind.EVIDENCE,
            f"{term_id}:{evidence_id}",
            f"{path_prefix}.evidence[{evidence_id}]",
            before_by_id[evidence_id],
        )
    for evidence_id in sorted(after_by_id.keys() - before_by_id.keys()):
        _append_added(
            changes,
            SemanticObjectKind.EVIDENCE,
            f"{term_id}:{evidence_id}",
            f"{path_prefix}.evidence[{evidence_id}]",
            after_by_id[evidence_id],
        )


def _diff_proposals(
    changes: list[SemanticDiffItem],
    before_proposals: Sequence[ProposalCandidate],
    after_proposals: Sequence[ProposalCandidate],
) -> None:
    before_by_id = {proposal.id: proposal for proposal in before_proposals}
    after_by_id = {proposal.id: proposal for proposal in after_proposals}
    for proposal_id in sorted(before_by_id.keys() - after_by_id.keys()):
        _append_removed(changes, SemanticObjectKind.PROPOSAL, proposal_id, f"proposals[{proposal_id}]", before_by_id[proposal_id].to_dict())
    for proposal_id in sorted(after_by_id.keys() - before_by_id.keys()):
        _append_added(changes, SemanticObjectKind.PROPOSAL, proposal_id, f"proposals[{proposal_id}]", after_by_id[proposal_id].to_dict())
    for proposal_id in sorted(before_by_id.keys() & after_by_id.keys()):
        before = before_by_id[proposal_id]
        after = after_by_id[proposal_id]
        path = f"proposals[{proposal_id}]"
        _diff_scalar_field(changes, SemanticObjectKind.PROPOSAL, proposal_id, path, "kind", before.kind.value, after.kind.value)
        _diff_scalar_field(changes, SemanticObjectKind.PROPOSAL, proposal_id, path, "surface", before.surface, after.surface)
        _diff_scalar_field(changes, SemanticObjectKind.PROPOSAL, proposal_id, path, "status", before.status.value, after.status.value)
        _diff_scalar_field(changes, SemanticObjectKind.PROPOSAL, proposal_id, path, "candidate_term_id", before.candidate_term_id, after.candidate_term_id)
        _diff_scalar_field(changes, SemanticObjectKind.PROPOSAL, proposal_id, path, "target_term_id", before.target_term_id, after.target_term_id)
        _diff_scalar_field(changes, SemanticObjectKind.PROPOSAL, proposal_id, path, "confidence", before.confidence, after.confidence)
        _diff_scalar_field(changes, SemanticObjectKind.PROPOSAL, proposal_id, path, "risk", before.risk.value, after.risk.value)
        _diff_sequence_field(changes, SemanticObjectKind.PROPOSAL, proposal_id, path, "scopes", before.scopes, after.scopes)
        _diff_scalar_field(changes, SemanticObjectKind.PROPOSAL, proposal_id, path, "rationale", before.rationale, after.rationale)
        _diff_mapping_field(changes, SemanticObjectKind.PROPOSAL, proposal_id, path, "metadata", before.metadata, after.metadata)
        _diff_evidence(changes, proposal_id, before.evidence, after.evidence, path_prefix=path)


def _diff_scalar_field(
    changes: list[SemanticDiffItem],
    object_kind: SemanticObjectKind,
    object_id: str,
    path: str,
    field: str,
    before: Any,
    after: Any,
) -> None:
    if before == after:
        return
    changes.append(
        SemanticDiffItem(
            change=SemanticChangeKind.CHANGED,
            object_kind=object_kind,
            object_id=object_id,
            path=path,
            field=field,
            before=before,
            after=after,
        )
    )


def _diff_sequence_field(
    changes: list[SemanticDiffItem],
    object_kind: SemanticObjectKind,
    object_id: str,
    path: str,
    field: str,
    before: Sequence[Any],
    after: Sequence[Any],
) -> None:
    before_value = list(before)
    after_value = list(after)
    if before_value == after_value:
        return
    changes.append(
        SemanticDiffItem(
            change=SemanticChangeKind.CHANGED,
            object_kind=object_kind,
            object_id=object_id,
            path=path,
            field=field,
            before=before_value,
            after=after_value,
        )
    )


def _diff_mapping_field(
    changes: list[SemanticDiffItem],
    object_kind: SemanticObjectKind,
    object_id: str,
    path: str,
    field: str,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> None:
    before_value = _json_ready(dict(before))
    after_value = _json_ready(dict(after))
    if before_value == after_value:
        return
    changes.append(
        SemanticDiffItem(
            change=SemanticChangeKind.CHANGED,
            object_kind=object_kind,
            object_id=object_id,
            path=path,
            field=field,
            before=before_value,
            after=after_value,
        )
    )


def _append_added(changes: list[SemanticDiffItem], object_kind: SemanticObjectKind, object_id: str, path: str, after: Any) -> None:
    changes.append(
        SemanticDiffItem(
            change=SemanticChangeKind.ADDED,
            object_kind=object_kind,
            object_id=object_id,
            path=path,
            after=after,
        )
    )


def _append_removed(changes: list[SemanticDiffItem], object_kind: SemanticObjectKind, object_id: str, path: str, before: Any) -> None:
    changes.append(
        SemanticDiffItem(
            change=SemanticChangeKind.REMOVED,
            object_kind=object_kind,
            object_id=object_id,
            path=path,
            before=before,
        )
    )


def _alias_key(alias: Alias) -> str:
    return alias.surface


def _evidence_key(evidence: Any) -> str:
    payload = evidence.to_dict()
    return "|".join(
        str(payload.get(name) or "")
        for name in ("source_path", "start_line", "end_line", "kind", "snippet")
    )


def _sort_changes(changes: Iterable[SemanticDiffItem]) -> list[SemanticDiffItem]:
    object_order = {item.value: index for index, item in enumerate(SemanticObjectKind)}
    change_order = {item.value: index for index, item in enumerate(SemanticChangeKind)}
    return sorted(
        changes,
        key=lambda item: (
            object_order[item.object_kind.value],
            item.object_id,
            item.path,
            change_order[item.change.value],
            item.field or "",
        ),
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
    "SemanticChangeKind",
    "SemanticDiffError",
    "SemanticDiffItem",
    "SemanticDiffReport",
    "SemanticDiffSummary",
    "SemanticObjectKind",
    "diff_lexicon_files",
    "diff_lexicons",
]
