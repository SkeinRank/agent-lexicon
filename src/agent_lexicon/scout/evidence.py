"""Evidence pack construction for local scout candidates.

Evidence packs collect line-numbered snippets around discovered candidate
surfaces. Positive snippets show where the exact surface appears. Negative
snippets show nearby project language that overlaps with the candidate tokens but
not with the exact surface, which helps reviewers detect ambiguity before a term
is promoted into the lexicon.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping

from agent_lexicon.ingest import IngestDocument
from agent_lexicon.safety import scan_prompt_injection_text
from agent_lexicon.scout.candidates import CandidateSurfaceKind, ScoutCandidate


class EvidencePackError(ValueError):
    """Raised when evidence pack construction receives invalid input."""


class EvidenceSnippetKind(str, Enum):
    """Evidence role for one line-numbered snippet."""

    POSITIVE = "positive"
    NEGATIVE = "negative"


@dataclass(frozen=True, slots=True)
class EvidenceSnippet:
    """One line-numbered snippet supporting or challenging a candidate."""

    document_path: str
    start_line: int
    end_line: int
    text: str
    kind: EvidenceSnippetKind
    reason: str
    matched_surface: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "document_path", _clean_text(self.document_path, field_name="document_path"))
        if self.start_line < 1:
            raise EvidencePackError("start_line must be greater than 0")
        if self.end_line < self.start_line:
            raise EvidencePackError("end_line must be greater than or equal to start_line")
        object.__setattr__(self, "text", _clean_text(self.text, field_name="text"))
        object.__setattr__(self, "kind", EvidenceSnippetKind(self.kind.value if isinstance(self.kind, EvidenceSnippetKind) else str(self.kind)))
        object.__setattr__(self, "reason", _clean_text(self.reason, field_name="reason"))
        if not isinstance(self.matched_surface, str):
            raise EvidencePackError("matched_surface must be a string")
        object.__setattr__(self, "matched_surface", self.matched_surface.strip())
        if not isinstance(self.metadata, Mapping):
            raise EvidencePackError("metadata must be a mapping")
        object.__setattr__(self, "metadata", {str(key): value for key, value in self.metadata.items()})

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable snippet representation."""
        return {
            "document_path": self.document_path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "text": self.text,
            "kind": self.kind.value,
            "reason": self.reason,
            "matched_surface": self.matched_surface,
            "metadata": dict(self.metadata),
        }

    def to_json_line(self) -> str:
        """Return this snippet as one JSONL row."""
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True, slots=True)
class EvidencePack:
    """Reviewable evidence collected for one scout candidate."""

    surface: str
    normalized_surface: str
    candidate_kind: CandidateSurfaceKind
    score: float
    positive_snippets: tuple[EvidenceSnippet, ...] = ()
    negative_snippets: tuple[EvidenceSnippet, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "surface", _clean_text(self.surface, field_name="surface"))
        object.__setattr__(self, "normalized_surface", _clean_text(self.normalized_surface, field_name="normalized_surface"))
        object.__setattr__(self, "candidate_kind", CandidateSurfaceKind(self.candidate_kind.value if isinstance(self.candidate_kind, CandidateSurfaceKind) else str(self.candidate_kind)))
        object.__setattr__(self, "score", _bounded_score(self.score))
        if not isinstance(self.positive_snippets, tuple):
            object.__setattr__(self, "positive_snippets", tuple(self.positive_snippets))
        if not isinstance(self.negative_snippets, tuple):
            object.__setattr__(self, "negative_snippets", tuple(self.negative_snippets))
        for snippet in (*self.positive_snippets, *self.negative_snippets):
            if not isinstance(snippet, EvidenceSnippet):
                raise EvidencePackError("evidence packs must contain EvidenceSnippet objects")
        if not isinstance(self.metadata, Mapping):
            raise EvidencePackError("metadata must be a mapping")
        object.__setattr__(self, "metadata", {str(key): value for key, value in self.metadata.items()})

    @property
    def positive_count(self) -> int:
        """Return the number of positive snippets."""
        return len(self.positive_snippets)

    @property
    def negative_count(self) -> int:
        """Return the number of negative snippets."""
        return len(self.negative_snippets)

    @property
    def snippet_count(self) -> int:
        """Return the total number of snippets."""
        return self.positive_count + self.negative_count

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable evidence pack representation."""
        return {
            "surface": self.surface,
            "normalized_surface": self.normalized_surface,
            "candidate_kind": self.candidate_kind.value,
            "score": self.score,
            "positive_count": self.positive_count,
            "negative_count": self.negative_count,
            "snippet_count": self.snippet_count,
            "positive_snippets": [snippet.to_dict() for snippet in self.positive_snippets],
            "negative_snippets": [snippet.to_dict() for snippet in self.negative_snippets],
            "metadata": dict(self.metadata),
        }

    def to_json_line(self) -> str:
        """Return this evidence pack as one JSONL row."""
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True, slots=True)
class EvidencePackReport:
    """Result returned by local evidence pack construction."""

    packs: tuple[EvidencePack, ...]
    document_count: int
    candidate_count: int
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.packs, tuple):
            object.__setattr__(self, "packs", tuple(self.packs))
        for pack in self.packs:
            if not isinstance(pack, EvidencePack):
                raise EvidencePackError("packs must contain EvidencePack objects")
        if self.document_count < 0:
            raise EvidencePackError("document_count must be greater than or equal to 0")
        if self.candidate_count < 0:
            raise EvidencePackError("candidate_count must be greater than or equal to 0")
        if not isinstance(self.metadata, Mapping):
            raise EvidencePackError("metadata must be a mapping")
        object.__setattr__(self, "metadata", {str(key): value for key, value in self.metadata.items()})

    @property
    def pack_count(self) -> int:
        """Return the number of evidence packs."""
        return len(self.packs)

    @property
    def positive_count(self) -> int:
        """Return the number of positive snippets across all packs."""
        return sum(pack.positive_count for pack in self.packs)

    @property
    def negative_count(self) -> int:
        """Return the number of negative snippets across all packs."""
        return sum(pack.negative_count for pack in self.packs)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable evidence report representation."""
        return {
            "pack_count": self.pack_count,
            "document_count": self.document_count,
            "candidate_count": self.candidate_count,
            "positive_count": self.positive_count,
            "negative_count": self.negative_count,
            "packs": [pack.to_dict() for pack in self.packs],
            "metadata": dict(self.metadata),
        }


def build_evidence_packs(
    documents: Iterable[IngestDocument],
    candidates: Iterable[ScoutCandidate],
    *,
    context_lines: int = 1,
    max_positive_snippets: int = 3,
    max_negative_snippets: int = 3,
    include_prompt_safety: bool = True,
) -> EvidencePackReport:
    """Build positive and negative evidence packs for scout candidates.

    Positive snippets are centered on the exact occurrences stored in each
    candidate. Negative snippets are selected from lines that share candidate
    tokens but do not contain the exact candidate surface.
    """
    if context_lines < 0:
        raise EvidencePackError("context_lines must be greater than or equal to 0")
    if max_positive_snippets < 1:
        raise EvidencePackError("max_positive_snippets must be greater than 0")
    if max_negative_snippets < 0:
        raise EvidencePackError("max_negative_snippets must be greater than or equal to 0")
    if not isinstance(include_prompt_safety, bool):
        raise EvidencePackError("include_prompt_safety must be a boolean")

    document_tuple = tuple(documents)
    candidate_tuple = tuple(candidates)
    for document in document_tuple:
        if not isinstance(document, IngestDocument):
            raise EvidencePackError("documents must contain IngestDocument objects")
    for candidate in candidate_tuple:
        if not isinstance(candidate, ScoutCandidate):
            raise EvidencePackError("candidates must contain ScoutCandidate objects")

    document_index = {document.relative_path: document for document in document_tuple}
    packs: list[EvidencePack] = []
    for candidate in candidate_tuple:
        positive_snippets = _build_positive_snippets(
            candidate,
            document_index=document_index,
            context_lines=context_lines,
            max_snippets=max_positive_snippets,
        )
        negative_snippets = _build_negative_snippets(
            candidate,
            documents=document_tuple,
            context_lines=context_lines,
            max_snippets=max_negative_snippets,
            positive_snippets=positive_snippets,
        )
        if include_prompt_safety:
            positive_snippets = _annotate_snippets_with_prompt_safety(positive_snippets)
            negative_snippets = _annotate_snippets_with_prompt_safety(negative_snippets)
        prompt_safety_summary = _prompt_safety_summary((*positive_snippets, *negative_snippets))
        packs.append(
            EvidencePack(
                surface=candidate.surface,
                normalized_surface=candidate.normalized_surface,
                candidate_kind=candidate.kind,
                score=candidate.score,
                positive_snippets=positive_snippets,
                negative_snippets=negative_snippets,
                metadata={
                    "occurrence_count": candidate.occurrence_count,
                    "document_count": candidate.document_count,
                    "jargon_score": candidate.jargon_score,
                    "background_penalty": candidate.background_penalty,
                    "candidate_metadata": dict(candidate.metadata),
                    "prompt_safety": prompt_safety_summary,
                },
            )
        )

    report_prompt_safety = _report_prompt_safety_summary(packs)
    return EvidencePackReport(
        packs=tuple(packs),
        document_count=len(document_tuple),
        candidate_count=len(candidate_tuple),
        metadata={
            "context_lines": context_lines,
            "max_positive_snippets": max_positive_snippets,
            "max_negative_snippets": max_negative_snippets,
            "include_prompt_safety": include_prompt_safety,
            "prompt_safety": report_prompt_safety,
        },
    )


def _annotate_snippets_with_prompt_safety(snippets: tuple[EvidenceSnippet, ...]) -> tuple[EvidenceSnippet, ...]:
    annotated: list[EvidenceSnippet] = []
    for snippet in snippets:
        report = scan_prompt_injection_text(
            snippet.text,
            source_path=snippet.document_path,
            start_line=snippet.start_line,
        )
        metadata = dict(snippet.metadata)
        metadata["prompt_safety"] = report.to_dict(include_findings=True)
        annotated.append(
            EvidenceSnippet(
                document_path=snippet.document_path,
                start_line=snippet.start_line,
                end_line=snippet.end_line,
                text=snippet.text,
                kind=snippet.kind,
                reason=snippet.reason,
                matched_surface=snippet.matched_surface,
                metadata=metadata,
            )
        )
    return tuple(annotated)


def _prompt_safety_summary(snippets: tuple[EvidenceSnippet, ...]) -> dict[str, Any]:
    highest_risk = "none"
    action = "allow"
    finding_count = 0
    high_count = 0
    medium_count = 0
    low_count = 0
    for snippet in snippets:
        prompt_safety = dict(snippet.metadata.get("prompt_safety", {}))
        if not prompt_safety:
            continue
        finding_count += int(prompt_safety.get("finding_count", 0))
        high_count += int(prompt_safety.get("high_count", 0))
        medium_count += int(prompt_safety.get("medium_count", 0))
        low_count += int(prompt_safety.get("low_count", 0))
        snippet_risk = str(prompt_safety.get("highest_risk", "none"))
        if _risk_rank(snippet_risk) > _risk_rank(highest_risk):
            highest_risk = snippet_risk
            action = str(prompt_safety.get("action", "allow"))
    return {
        "finding_count": finding_count,
        "highest_risk": highest_risk,
        "action": action,
        "high_count": high_count,
        "medium_count": medium_count,
        "low_count": low_count,
        "is_safe_for_llm_review": action != "block_llm_review",
    }


def _report_prompt_safety_summary(packs: list[EvidencePack]) -> dict[str, Any]:
    highest_risk = "none"
    action = "allow"
    finding_count = 0
    high_count = 0
    medium_count = 0
    low_count = 0
    unsafe_pack_count = 0
    for pack in packs:
        summary = dict(pack.metadata.get("prompt_safety", {}))
        finding_count += int(summary.get("finding_count", 0))
        high_count += int(summary.get("high_count", 0))
        medium_count += int(summary.get("medium_count", 0))
        low_count += int(summary.get("low_count", 0))
        if summary.get("is_safe_for_llm_review") is False:
            unsafe_pack_count += 1
        pack_risk = str(summary.get("highest_risk", "none"))
        if _risk_rank(pack_risk) > _risk_rank(highest_risk):
            highest_risk = pack_risk
            action = str(summary.get("action", "allow"))
    return {
        "finding_count": finding_count,
        "highest_risk": highest_risk,
        "action": action,
        "high_count": high_count,
        "medium_count": medium_count,
        "low_count": low_count,
        "unsafe_pack_count": unsafe_pack_count,
        "is_safe_for_llm_review": action != "block_llm_review",
    }


def _risk_rank(value: str) -> int:
    return {"none": 0, "low": 1, "medium": 2, "high": 3}.get(value, 0)


def _build_positive_snippets(
    candidate: ScoutCandidate,
    *,
    document_index: Mapping[str, IngestDocument],
    context_lines: int,
    max_snippets: int,
) -> tuple[EvidenceSnippet, ...]:
    snippets: list[EvidenceSnippet] = []
    seen: set[tuple[str, int, int, EvidenceSnippetKind]] = set()
    for occurrence in candidate.occurrences:
        if len(snippets) >= max_snippets:
            break
        document = document_index.get(occurrence.document_path)
        if document is None:
            snippet = EvidenceSnippet(
                document_path=occurrence.document_path,
                start_line=occurrence.line_number,
                end_line=occurrence.line_number,
                text=occurrence.line_text,
                kind=EvidenceSnippetKind.POSITIVE,
                reason="candidate_surface_present",
                matched_surface=candidate.surface,
                metadata={"source": "candidate_occurrence"},
            )
        else:
            snippet = _snippet_from_document(
                document,
                center_line=occurrence.line_number,
                context_lines=context_lines,
                kind=EvidenceSnippetKind.POSITIVE,
                reason="candidate_surface_present",
                matched_surface=candidate.surface,
                metadata={"source": "candidate_occurrence"},
            )
        key = (snippet.document_path, snippet.start_line, snippet.end_line, snippet.kind)
        if key in seen:
            continue
        seen.add(key)
        snippets.append(snippet)
    return tuple(snippets)


def _build_negative_snippets(
    candidate: ScoutCandidate,
    *,
    documents: tuple[IngestDocument, ...],
    context_lines: int,
    max_snippets: int,
    positive_snippets: tuple[EvidenceSnippet, ...],
) -> tuple[EvidenceSnippet, ...]:
    if max_snippets == 0:
        return ()
    candidate_tokens = _surface_tokens_for_overlap(candidate.surface)
    if not candidate_tokens:
        return ()
    positive_ranges = {
        (snippet.document_path, line_number)
        for snippet in positive_snippets
        for line_number in range(snippet.start_line, snippet.end_line + 1)
    }
    exact_surface = _normalize_surface(candidate.surface)
    snippets: list[EvidenceSnippet] = []
    seen: set[tuple[str, int, int]] = set()

    for document in documents:
        lines = document.text.splitlines()
        for index, line in enumerate(lines, start=1):
            if len(snippets) >= max_snippets:
                return tuple(snippets)
            if (document.relative_path, index) in positive_ranges:
                continue
            normalized_line = _normalize_surface(line)
            if exact_surface and exact_surface in normalized_line:
                continue
            line_tokens = _surface_tokens_for_overlap(line)
            overlap = tuple(token for token in candidate_tokens if token in line_tokens)
            if not _is_negative_overlap(candidate_tokens, overlap):
                continue
            snippet = _snippet_from_document(
                document,
                center_line=index,
                context_lines=context_lines,
                kind=EvidenceSnippetKind.NEGATIVE,
                reason="partial_token_overlap_without_surface",
                matched_surface=" ".join(overlap),
                metadata={"overlap_tokens": list(overlap)},
            )
            key = (snippet.document_path, snippet.start_line, snippet.end_line)
            if key in seen:
                continue
            seen.add(key)
            snippets.append(snippet)
    return tuple(snippets)


def _snippet_from_document(
    document: IngestDocument,
    *,
    center_line: int,
    context_lines: int,
    kind: EvidenceSnippetKind,
    reason: str,
    matched_surface: str,
    metadata: Mapping[str, Any],
) -> EvidenceSnippet:
    lines = document.text.splitlines()
    if not lines:
        raise EvidencePackError(f"document has no lines: {document.relative_path}")
    bounded_center = max(1, min(center_line, len(lines)))
    start_line = max(1, bounded_center - context_lines)
    end_line = min(len(lines), bounded_center + context_lines)
    snippet_text = "\n".join(lines[start_line - 1 : end_line]).strip()
    return EvidenceSnippet(
        document_path=document.relative_path,
        start_line=start_line,
        end_line=end_line,
        text=snippet_text,
        kind=kind,
        reason=reason,
        matched_surface=matched_surface,
        metadata=metadata,
    )


def _is_negative_overlap(candidate_tokens: tuple[str, ...], overlap: tuple[str, ...]) -> bool:
    if not overlap:
        return False
    if len(candidate_tokens) == 1:
        return len(overlap[0]) >= 4
    return len(overlap) >= max(1, min(2, len(candidate_tokens) - 1))


def _surface_tokens_for_overlap(value: str) -> tuple[str, ...]:
    split_camel = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    raw_tokens = re.split(r"[^A-Za-z0-9]+", split_camel)
    return tuple(token.casefold() for token in raw_tokens if len(token) >= 3)


def _normalize_surface(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _clean_text(value: str, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise EvidencePackError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise EvidencePackError(f"{field_name} must not be empty")
    return cleaned


def _bounded_score(value: float) -> float:
    if not isinstance(value, (int, float)):
        raise EvidencePackError("score must be a number")
    as_float = float(value)
    if as_float < 0 or as_float > 1:
        raise EvidencePackError("score must be between 0 and 1")
    return as_float


__all__ = [
    "EvidencePack",
    "EvidencePackError",
    "EvidencePackReport",
    "EvidenceSnippet",
    "EvidenceSnippetKind",
    "build_evidence_packs",
]
