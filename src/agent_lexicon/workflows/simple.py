"""Simple local workflows for Agent Lexicon.

These helpers provide the short product-facing commands that sit on top of the
lower-level runtime, scout, workspace, review, and snapshot APIs. They are
intended for the common local loop:

``agent-lexicon init`` -> ``agent-lexicon scan docs src`` ->
``agent-lexicon analyze`` -> ``agent-lexicon publish``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from agent_lexicon.core import AgentLexiconLoadError, load_lexicon
from agent_lexicon.dictionary import (
    DEFAULT_DICTIONARY_DIR,
    DictionaryLayoutError,
    DictionaryLayoutSummary,
    dictionary_layout_path,
    init_dictionary_layout,
    inspect_dictionary_layout,
)
from agent_lexicon.ingest import LocalIngestError, LocalIngestReport, ingest_local_paths
from agent_lexicon.policy import LocalPolicy, LocalPolicyError, init_local_policy, load_local_policy, policy_path
from agent_lexicon.review_agent import ReviewAgentDecision, ReviewAgentError, run_review_agent
from agent_lexicon.safety import PromptSafetyError, PromptSafetyReport, scan_documents_for_prompt_injection
from agent_lexicon.scout import (
    EvidencePackError,
    EvidencePackReport,
    ScoutCandidateError,
    ScoutCandidateReport,
    build_evidence_packs,
    discover_scout_candidates,
    existing_surfaces_from_lexicon,
)
from agent_lexicon.workspace import SnapshotPublishError, WorkspaceError, WorkspaceState, WorkspaceSummary, init_workspace, open_workspace, publish_local_snapshot


DEFAULT_SCAN_PATHS = ("README.md", "docs", "src")


class SimpleWorkflowError(ValueError):
    """Raised when a simple local workflow cannot be completed."""


@dataclass(frozen=True, slots=True)
class SimpleInitReport:
    """Result returned by the product-facing init workflow."""

    dictionary: DictionaryLayoutSummary
    workspace: WorkspaceSummary
    policy_path: str
    policy_mode: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable init report."""
        return {
            "dictionary": self.dictionary.to_dict(),
            "workspace": self.workspace.to_dict(),
            "policy_path": self.policy_path,
            "policy_mode": self.policy_mode,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class SimpleScanReport:
    """Result returned by the product-facing scan workflow."""

    ingest: LocalIngestReport
    safety: PromptSafetyReport
    candidates: ScoutCandidateReport
    evidence: EvidencePackReport
    workspace: WorkspaceSummary
    lexicon_path: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def document_count(self) -> int:
        """Return the number of ingested documents."""
        return self.ingest.document_count

    @property
    def candidate_count(self) -> int:
        """Return the number of stored candidates."""
        return self.candidates.candidate_count

    @property
    def evidence_pack_count(self) -> int:
        """Return the number of stored evidence packs."""
        return self.evidence.pack_count

    def to_dict(self, *, include_documents: bool = False) -> dict[str, Any]:
        """Return a JSON-serializable scan report."""
        return {
            "document_count": self.document_count,
            "candidate_count": self.candidate_count,
            "evidence_pack_count": self.evidence_pack_count,
            "positive_count": self.evidence.positive_count,
            "negative_count": self.evidence.negative_count,
            "lexicon_path": self.lexicon_path,
            "ingest": self.ingest.to_dict(include_text=include_documents),
            "safety": self.safety.to_dict(),
            "candidates": self.candidates.to_dict(),
            "evidence": self.evidence.to_dict(),
            "workspace": self.workspace.to_dict(),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class SimpleAnalysisItem:
    """One prioritized workspace item for the analyze command."""

    surface: str
    normalized_surface: str
    priority: str
    priority_score: float
    review_status: str
    candidate_kind: str
    score: float
    jargon_score: float
    background_penalty: float
    positive_count: int
    negative_count: int
    document_count: int
    priority_reasons: tuple[str, ...] = ()
    cluster_key: str | None = None
    cluster_size: int = 1
    oov_proxy_score: float = 0.0
    token_fragmentation_score: float = 0.0
    surface_risk_score: float = 0.0
    recommendation: str | None = None
    reviewer_note: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable analysis item."""
        return {
            "surface": self.surface,
            "normalized_surface": self.normalized_surface,
            "priority": self.priority,
            "priority_score": self.priority_score,
            "priority_reasons": list(self.priority_reasons),
            "cluster_key": self.cluster_key,
            "cluster_size": self.cluster_size,
            "oov_proxy_score": self.oov_proxy_score,
            "token_fragmentation_score": self.token_fragmentation_score,
            "surface_risk_score": self.surface_risk_score,
            "review_status": self.review_status,
            "candidate_kind": self.candidate_kind,
            "score": self.score,
            "jargon_score": self.jargon_score,
            "background_penalty": self.background_penalty,
            "positive_count": self.positive_count,
            "negative_count": self.negative_count,
            "document_count": self.document_count,
            "recommendation": self.recommendation,
            "reviewer_note": self.reviewer_note,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class SimpleAnalyzeReport:
    """Result returned by the product-facing analyze workflow."""

    items: tuple[SimpleAnalysisItem, ...]
    workspace: WorkspaceSummary
    review_agent_enabled: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def item_count(self) -> int:
        """Return the number of analysis items."""
        return len(self.items)

    @property
    def important_count(self) -> int:
        """Return the number of important items."""
        return sum(1 for item in self.items if item.priority == "important")

    @property
    def later_count(self) -> int:
        """Return the number of later-priority items."""
        return sum(1 for item in self.items if item.priority == "later")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable analysis report."""
        return {
            "item_count": self.item_count,
            "important_count": self.important_count,
            "later_count": self.later_count,
            "review_agent_enabled": self.review_agent_enabled,
            "items": [item.to_dict() for item in self.items],
            "workspace": self.workspace.to_dict(),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class SimplePublishReport:
    """Result returned by the product-facing publish workflow."""

    snapshot_id: str
    output_path: str
    term_count: int
    accepted_count: int
    generated_term_count: int
    skipped_count: int
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable publish report."""
        return {
            "snapshot_id": self.snapshot_id,
            "output_path": self.output_path,
            "term_count": self.term_count,
            "accepted_count": self.accepted_count,
            "generated_term_count": self.generated_term_count,
            "skipped_count": self.skipped_count,
            "metadata": dict(self.metadata),
        }


def run_simple_init(
    root: str | Path = ".",
    *,
    layout_dir: str = DEFAULT_DICTIONARY_DIR,
    policy_mode: str = "solo",
    actor: str = "local",
    role: str = "owner",
    force: bool = False,
    reset_workspace: bool = False,
) -> SimpleInitReport:
    """Initialize the default local Agent Lexicon layout and workspace."""
    root_path = Path(root).expanduser().resolve()
    try:
        dictionary = init_dictionary_layout(root_path, layout_dir=layout_dir, force=force)
        workspace = init_workspace(root_path, reset=reset_workspace)
        policy_file = policy_path(root_path)
        if force or not policy_file.exists():
            policy = init_local_policy(root_path, mode=policy_mode, actor=actor, role=role, force=force)
        else:
            policy = load_local_policy(root_path, mode=policy_mode)
    except (DictionaryLayoutError, WorkspaceError, LocalPolicyError) as exc:
        raise SimpleWorkflowError(str(exc)) from exc
    return SimpleInitReport(
        dictionary=dictionary,
        workspace=workspace.summary(),
        policy_path=str(policy_file),
        policy_mode=policy.mode.value,
        metadata={"root": str(root_path), "layout_dir": layout_dir},
    )


def run_simple_scan(
    paths: Sequence[str | Path] | None = None,
    *,
    root: str | Path = ".",
    layout_dir: str = DEFAULT_DICTIONARY_DIR,
    lexicon_path: str | Path | None = None,
    include_globs: Sequence[str] | None = None,
    min_score: float = 0.25,
    max_candidates: int = 20,
    context_lines: int = 1,
    max_positive_snippets: int = 3,
    max_negative_snippets: int = 3,
    max_file_bytes: int = 1_000_000,
) -> SimpleScanReport:
    """Run local ingest, safety scan, candidate discovery, evidence, and workspace sync."""
    root_path = Path(root).expanduser().resolve()
    resolved_paths = _resolve_scan_paths(paths, root=root_path)
    resolved_lexicon_path = _resolve_default_lexicon_path(root_path, layout_dir=layout_dir, lexicon_path=lexicon_path)

    try:
        ingest = ingest_local_paths(
            resolved_paths,
            root=root_path,
            include_globs=tuple(include_globs) if include_globs is not None else None,
            max_file_bytes=max_file_bytes,
        )
        safety = scan_documents_for_prompt_injection(ingest.documents)
        existing_surfaces = ()
        if resolved_lexicon_path is not None:
            lexicon = load_lexicon(resolved_lexicon_path)
            existing_surfaces = existing_surfaces_from_lexicon(lexicon)
        candidates = discover_scout_candidates(
            ingest.documents,
            existing_surfaces=existing_surfaces,
            min_score=min_score,
            max_candidates=max_candidates,
        )
        evidence = build_evidence_packs(
            ingest.documents,
            candidates.candidates,
            context_lines=context_lines,
            max_positive_snippets=max_positive_snippets,
            max_negative_snippets=max_negative_snippets,
            include_prompt_safety=True,
        )
        state = init_workspace(root_path)
        state.store_ingest_report(ingest)
        state.store_candidate_report(candidates)
        state.store_evidence_report(evidence)
    except (
        LocalIngestError,
        PromptSafetyError,
        AgentLexiconLoadError,
        ScoutCandidateError,
        EvidencePackError,
        WorkspaceError,
    ) as exc:
        raise SimpleWorkflowError(str(exc)) from exc

    return SimpleScanReport(
        ingest=ingest,
        safety=safety,
        candidates=candidates,
        evidence=evidence,
        workspace=state.summary(),
        lexicon_path=str(resolved_lexicon_path) if resolved_lexicon_path is not None else None,
        metadata={"root": str(root_path), "paths": [str(path) for path in resolved_paths]},
    )


def run_simple_analyze(
    root: str | Path = ".",
    *,
    limit: int = 10,
    include_review_agent: bool = False,
    priority: str = "all",
) -> SimpleAnalyzeReport:
    """Summarize the highest-priority local review items."""
    if limit < 1:
        raise SimpleWorkflowError("limit must be greater than 0")
    if priority not in {"all", "important", "later"}:
        raise SimpleWorkflowError("priority must be one of: all, important, later")
    root_path = Path(root).expanduser().resolve()
    try:
        state = open_workspace(root_path, create=False)
        review_items = state.list_review_items(limit=max(limit, 100))
    except WorkspaceError as exc:
        raise SimpleWorkflowError(str(exc)) from exc

    items: list[SimpleAnalysisItem] = []
    for review_item in review_items:
        decision: ReviewAgentDecision | None = None
        if include_review_agent:
            try:
                decision = run_review_agent(review_item)
            except ReviewAgentError:
                decision = None
        quality = _quality_metadata(review_item)
        priority_score = _priority_score(review_item, quality=quality)
        priority = str(quality.get("priority") or ("important" if priority_score >= 0.55 else "later"))
        if priority not in {"important", "later"}:
            priority = "important" if priority_score >= 0.55 else "later"
        cluster = _cluster_metadata(review_item)
        items.append(
            SimpleAnalysisItem(
                surface=review_item.surface,
                normalized_surface=review_item.normalized_surface,
                priority=priority,
                priority_score=priority_score,
                priority_reasons=tuple(str(reason) for reason in quality.get("priority_reasons", [])),
                cluster_key=str(quality.get("cluster_key") or cluster.get("cluster_key") or "") or None,
                cluster_size=int(cluster.get("candidate_count", quality.get("metadata", {}).get("cluster_size", 1)) or 1),
                oov_proxy_score=float(quality.get("oov_proxy_score", 0.0) or 0.0),
                token_fragmentation_score=float(quality.get("token_fragmentation_score", 0.0) or 0.0),
                surface_risk_score=float(quality.get("surface_risk_score", 0.0) or 0.0),
                review_status=review_item.review_decision.decision.value if review_item.review_decision else "unreviewed",
                candidate_kind=review_item.candidate_kind,
                score=review_item.score,
                jargon_score=review_item.jargon_score,
                background_penalty=review_item.background_penalty,
                positive_count=review_item.positive_count,
                negative_count=review_item.negative_count,
                document_count=review_item.document_count,
                recommendation=decision.recommendation.value if decision else None,
                reviewer_note=decision.reviewer_note if decision else None,
                metadata={"occurrence_count": review_item.occurrence_count, "quality": quality, "cluster": cluster},
            )
        )
    items.sort(key=lambda item: (item.priority != "important", -item.priority_score, item.surface.casefold()))
    if priority != "all":
        items = [item for item in items if item.priority == priority]
    return SimpleAnalyzeReport(
        items=tuple(items[:limit]),
        workspace=state.summary(),
        review_agent_enabled=include_review_agent,
        metadata={"root": str(root_path), "priority_filter": priority},
    )


def run_simple_publish(
    root: str | Path = ".",
    *,
    layout_dir: str = DEFAULT_DICTIONARY_DIR,
    lexicon_path: str | Path | None = None,
    output_path: str | Path | None = None,
    snapshot_id: str | None = None,
) -> SimplePublishReport:
    """Publish accepted local review decisions as a lexicon-compatible snapshot."""
    root_path = Path(root).expanduser().resolve()
    resolved_lexicon_path = _resolve_default_lexicon_path(root_path, layout_dir=layout_dir, lexicon_path=lexicon_path)
    try:
        state = open_workspace(root_path, create=False)
        base_lexicon = load_lexicon(resolved_lexicon_path) if resolved_lexicon_path is not None else None
        snapshot = publish_local_snapshot(
            state,
            output_path=Path(output_path) if output_path is not None else None,
            base_lexicon=base_lexicon,
            snapshot_id=snapshot_id,
        )
    except (WorkspaceError, AgentLexiconLoadError, SnapshotPublishError, OSError) as exc:
        raise SimpleWorkflowError(str(exc)) from exc
    return SimplePublishReport(
        snapshot_id=snapshot.snapshot_id,
        output_path=snapshot.output_path,
        term_count=snapshot.term_count,
        accepted_count=snapshot.accepted_count,
        generated_term_count=snapshot.generated_term_count,
        skipped_count=snapshot.skipped_count,
        metadata={"root": str(root_path), "lexicon_path": str(resolved_lexicon_path) if resolved_lexicon_path else None},
    )


def _resolve_scan_paths(paths: Sequence[str | Path] | None, *, root: Path) -> tuple[Path, ...]:
    raw_paths = tuple(paths) if paths else DEFAULT_SCAN_PATHS
    resolved: list[Path] = []
    missing: list[str] = []
    for raw_path in raw_paths:
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = root / path
        if path.exists():
            resolved.append(path)
        else:
            missing.append(str(raw_path))
    if not resolved:
        raise SimpleWorkflowError(
            "no scan paths exist; pass files or directories explicitly, for example: agent-lexicon scan README.md docs src"
        )
    return tuple(resolved)


def _resolve_default_lexicon_path(
    root: Path,
    *,
    layout_dir: str,
    lexicon_path: str | Path | None,
) -> Path | None:
    if lexicon_path is not None:
        path = Path(lexicon_path).expanduser()
        return path if path.is_absolute() else root / path
    layout = dictionary_layout_path(root, layout_dir=layout_dir)
    default_path = Path(layout.lexicon_path)
    return default_path if default_path.exists() else None


def _priority_score(review_item: Any, *, quality: Mapping[str, Any] | None = None) -> float:
    quality = quality or _quality_metadata(review_item)
    if "priority_score" in quality:
        try:
            return max(0.0, min(1.0, round(float(quality["priority_score"]), 4)))
        except (TypeError, ValueError):
            pass
    score = float(review_item.score) * 0.36
    score += float(review_item.jargon_score) * 0.20
    score += float(quality.get("oov_proxy_score", 0.0) or 0.0) * 0.16
    score += float(quality.get("surface_risk_score", 0.0) or 0.0) * 0.12
    score += min(float(review_item.document_count) / 3.0, 1.0) * 0.08
    score += 0.06 if int(review_item.negative_count) > 0 else 0.0
    score += 0.08 if str(review_item.candidate_kind) in {"identifier", "acronym", "code"} else 0.0
    score -= float(review_item.background_penalty) * 0.10
    return max(0.0, min(1.0, round(score, 4)))


def _quality_metadata(review_item: Any) -> Mapping[str, Any]:
    payload = getattr(review_item, "candidate_payload", {})
    if isinstance(payload, Mapping):
        metadata = payload.get("metadata", {})
        if isinstance(metadata, Mapping):
            quality = metadata.get("quality", {})
            if isinstance(quality, Mapping):
                return quality
    return {}


def _cluster_metadata(review_item: Any) -> Mapping[str, Any]:
    payload = getattr(review_item, "candidate_payload", {})
    if isinstance(payload, Mapping):
        metadata = payload.get("metadata", {})
        if isinstance(metadata, Mapping):
            cluster = metadata.get("cluster", {})
            if isinstance(cluster, Mapping):
                return cluster
    return {}


__all__ = [
    "DEFAULT_SCAN_PATHS",
    "SimpleAnalyzeReport",
    "SimpleAnalysisItem",
    "SimpleInitReport",
    "SimplePublishReport",
    "SimpleScanReport",
    "SimpleWorkflowError",
    "run_simple_analyze",
    "run_simple_init",
    "run_simple_publish",
    "run_simple_scan",
]
