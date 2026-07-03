"""Immutable lexicon snapshot metadata helpers.

The runtime treats a loaded lexicon as an immutable input. These helpers attach
stable content-addressed metadata to runtime decisions and review reports so a
caller can reproduce a decision later with the same lexicon content.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import Lexicon

LEXICON_FINGERPRINT_ALGORITHM = "sha256"
LEXICON_SNAPSHOT_REF_PREFIX = f"{LEXICON_FINGERPRINT_ALGORITHM}:"


@dataclass(frozen=True, slots=True)
class LexiconFingerprint:
    """Stable digest for a loaded lexicon document."""

    algorithm: str
    value: str
    version: str
    term_count: int
    scope_count: int
    proposal_count: int
    surface_count: int

    @property
    def ref(self) -> str:
        """Return the content-addressed snapshot reference."""
        return f"{self.algorithm}:{self.value}"

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible fingerprint payload."""
        return {
            "algorithm": self.algorithm,
            "value": self.value,
            "ref": self.ref,
            "version": self.version,
            "term_count": self.term_count,
            "scope_count": self.scope_count,
            "proposal_count": self.proposal_count,
            "surface_count": self.surface_count,
        }


@dataclass(frozen=True, slots=True)
class LexiconSnapshotMetadata:
    """Immutable runtime metadata for one loaded lexicon snapshot."""

    fingerprint: LexiconFingerprint
    source_path: str | None = None
    immutable: bool = True

    @property
    def ref(self) -> str:
        """Return the content-addressed snapshot reference."""
        return self.fingerprint.ref

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible metadata payload."""
        payload: dict[str, object] = {
            "ref": self.ref,
            "immutable": self.immutable,
            "fingerprint": self.fingerprint.to_dict(),
            "fingerprint_algorithm": self.fingerprint.algorithm,
            "fingerprint_value": self.fingerprint.value,
            "lexicon_version": self.fingerprint.version,
            "term_count": self.fingerprint.term_count,
            "scope_count": self.fingerprint.scope_count,
            "proposal_count": self.fingerprint.proposal_count,
            "surface_count": self.fingerprint.surface_count,
        }
        if self.source_path is not None:
            payload["source_path"] = self.source_path
        return payload


def fingerprint_lexicon(lexicon: Lexicon) -> LexiconFingerprint:
    """Return a stable content fingerprint for a loaded lexicon."""
    if not isinstance(lexicon, Lexicon):
        raise TypeError("lexicon must be a Lexicon")
    payload = json.dumps(
        lexicon.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return LexiconFingerprint(
        algorithm=LEXICON_FINGERPRINT_ALGORITHM,
        value=digest,
        version=lexicon.version,
        term_count=len(lexicon.terms),
        scope_count=len(lexicon.scopes),
        proposal_count=len(lexicon.proposals),
        surface_count=sum(len(term.surfaces(include_deprecated=True)) for term in lexicon.terms),
    )


def lexicon_snapshot_ref(lexicon: Lexicon) -> str:
    """Return ``sha256:<digest>`` for a loaded lexicon."""
    return fingerprint_lexicon(lexicon).ref


def lexicon_snapshot_metadata(lexicon: Lexicon, *, source_path: str | Path | None = None) -> LexiconSnapshotMetadata:
    """Return immutable snapshot metadata for a loaded lexicon."""
    resolved_source = str(source_path) if source_path is not None else None
    return LexiconSnapshotMetadata(
        fingerprint=fingerprint_lexicon(lexicon),
        source_path=resolved_source,
    )


def lexicon_runtime_metadata(lexicon: Lexicon, *, source_path: str | Path | None = None) -> dict[str, Any]:
    """Return flat metadata fields used by runtime decisions and reports."""
    snapshot = lexicon_snapshot_metadata(lexicon, source_path=source_path)
    return {
        "lexicon_snapshot": snapshot.to_dict(),
        "lexicon_snapshot_ref": snapshot.ref,
        "lexicon_fingerprint": snapshot.fingerprint.value,
        "lexicon_fingerprint_algorithm": snapshot.fingerprint.algorithm,
        "lexicon_version": snapshot.fingerprint.version,
    }


__all__ = [
    "LEXICON_FINGERPRINT_ALGORITHM",
    "LEXICON_SNAPSHOT_REF_PREFIX",
    "LexiconFingerprint",
    "LexiconSnapshotMetadata",
    "fingerprint_lexicon",
    "lexicon_runtime_metadata",
    "lexicon_snapshot_metadata",
    "lexicon_snapshot_ref",
]
