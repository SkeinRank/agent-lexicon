"""Optional OOV/tokenizer scoring for local Scout candidate quality.

The default scorer is dependency-free and stays suitable for local/offline use.
Projects that want a tokenizer-backed signal can install the optional ``oov``
extra and request a Hugging Face tokenizer such as ``BAAI/bge-small-en-v1.5``.
This module is intentionally Scout-only: runtime matching, resolving, and tool
protection stay deterministic and do not depend on tokenizer packages.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence

from agent_lexicon.text import surface_fragments


DEFAULT_OOV_TOKENIZER = "BAAI/bge-small-en-v1.5"


class OovScorerError(ValueError):
    """Raised when an optional OOV scorer cannot be initialized or used."""


class OovScorer(Protocol):
    """Protocol for Scout OOV scorers."""

    def score(self, surface: str) -> "OovScoreResult":
        """Return an OOV score for one candidate surface."""


@dataclass(frozen=True, slots=True)
class OovScoreResult:
    """Tokenizer or proxy OOV score for one candidate surface."""

    surface: str
    score: float
    source: str
    tokenizer_name: str | None = None
    token_count: int = 0
    fragment_count: int = 0
    continuation_token_count: int = 0
    unknown_token_count: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "surface", _clean_text(self.surface, field_name="surface"))
        object.__setattr__(self, "score", _bounded_float(self.score, field_name="score"))
        object.__setattr__(self, "source", _clean_text(self.source, field_name="source"))
        if self.tokenizer_name is not None:
            object.__setattr__(self, "tokenizer_name", _clean_text(self.tokenizer_name, field_name="tokenizer_name"))
        for field_name in ("token_count", "fragment_count", "continuation_token_count", "unknown_token_count"):
            value = int(getattr(self, field_name))
            if value < 0:
                raise OovScorerError(f"{field_name} must be greater than or equal to 0")
            object.__setattr__(self, field_name, value)
        if not isinstance(self.metadata, Mapping):
            raise OovScorerError("metadata must be a mapping")
        object.__setattr__(self, "metadata", {str(key): value for key, value in self.metadata.items()})

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "surface": self.surface,
            "score": self.score,
            "source": self.source,
            "tokenizer_name": self.tokenizer_name,
            "token_count": self.token_count,
            "fragment_count": self.fragment_count,
            "continuation_token_count": self.continuation_token_count,
            "unknown_token_count": self.unknown_token_count,
            "metadata": dict(self.metadata),
        }


class ProxyOovScorer:
    """Dependency-free OOV proxy used by default."""

    def __init__(self, *, source: str = "proxy", metadata: Mapping[str, Any] | None = None) -> None:
        self.source = source
        self.metadata = {str(key): value for key, value in (metadata or {}).items()}

    def score(self, surface: str) -> OovScoreResult:
        """Score tokenizer risk using surface shape and identifier fragments."""
        cleaned = _clean_text(surface, field_name="surface")
        fragments = surface_fragments(cleaned)
        separator_count = sum(1 for char in cleaned if not char.isalnum() and not char.isspace())
        camel_boundary_count = len(re.findall(r"[a-z0-9][A-Z]", cleaned))
        digit_count = sum(1 for char in cleaned if char.isdigit())
        acronym_bonus = 1 if re.search(r"\b[A-Z0-9]{3,}\b", cleaned) else 0
        fragment_component = min(1.0, max(0, len(fragments) - 1) / 4.0)
        shape_component = min(1.0, (separator_count + camel_boundary_count + acronym_bonus) / 4.0)
        digit_component = min(1.0, digit_count / 4.0) * 0.35
        score = round(max(0.0, min(1.0, (0.58 * fragment_component) + (0.34 * shape_component) + digit_component)), 4)
        metadata = dict(self.metadata)
        metadata.update(
            {
                "separator_count": separator_count,
                "camel_boundary_count": camel_boundary_count,
                "digit_count": digit_count,
                "acronym_bonus": acronym_bonus,
            }
        )
        return OovScoreResult(
            surface=cleaned,
            score=score,
            source=self.source,
            token_count=len(fragments),
            fragment_count=len(fragments),
            metadata=metadata,
        )


class TokenizerOovScorer:
    """Tokenizer-backed OOV scorer for optional Scout quality ranking."""

    def __init__(
        self,
        tokenizer_name: str = DEFAULT_OOV_TOKENIZER,
        *,
        tokenizer: Any | None = None,
    ) -> None:
        self.tokenizer_name = _clean_text(tokenizer_name, field_name="tokenizer_name")
        self._tokenizer = tokenizer if tokenizer is not None else _load_tokenizer(self.tokenizer_name)

    def score(self, surface: str) -> OovScoreResult:
        """Score a surface using tokenizer fragmentation and unknown-token signals."""
        cleaned = _clean_text(surface, field_name="surface")
        fragments = surface_fragments(cleaned)
        tokens = _tokenize(self._tokenizer, cleaned)
        token_count = len(tokens)
        fragment_count = len(fragments)
        continuation_count = sum(1 for token in tokens if _is_continuation_token(token))
        unknown_count = sum(1 for token in tokens if _is_unknown_token(token))
        expected_units = max(1, fragment_count)
        extra_token_component = min(1.0, max(0, token_count - expected_units) / max(2.0, expected_units * 2.0))
        continuation_component = min(1.0, continuation_count / max(1, token_count))
        unknown_component = min(1.0, unknown_count / max(1, token_count))
        shape_component = ProxyOovScorer().score(cleaned).score
        score = round(
            max(
                0.0,
                min(
                    1.0,
                    (0.52 * extra_token_component)
                    + (0.22 * unknown_component)
                    + (0.16 * continuation_component)
                    + (0.10 * shape_component),
                ),
            ),
            4,
        )
        return OovScoreResult(
            surface=cleaned,
            score=score,
            source="tokenizer",
            tokenizer_name=self.tokenizer_name,
            token_count=token_count,
            fragment_count=fragment_count,
            continuation_token_count=continuation_count,
            unknown_token_count=unknown_count,
            metadata={"tokens": list(tokens[:20]), "shape_proxy_score": shape_component},
        )


def build_oov_scorer(
    tokenizer_name: str | None = None,
    *,
    fallback_to_proxy: bool = True,
) -> OovScorer:
    """Build an OOV scorer.

    ``None`` and ``"proxy"`` return the dependency-free proxy scorer. ``"auto"``
    loads the default BGE-small tokenizer when the optional ``oov`` extra is
    installed. If tokenizer loading fails and fallback is enabled, a proxy scorer
    is returned with fallback metadata instead of breaking local scans.
    """
    if tokenizer_name is None or not str(tokenizer_name).strip() or str(tokenizer_name).strip().casefold() == "proxy":
        return ProxyOovScorer()
    requested = str(tokenizer_name).strip()
    resolved_name = DEFAULT_OOV_TOKENIZER if requested.casefold() == "auto" else requested
    try:
        return TokenizerOovScorer(resolved_name)
    except Exception as exc:  # pragma: no cover - depends on optional packages/network/cache
        if not fallback_to_proxy:
            if isinstance(exc, OovScorerError):
                raise
            raise OovScorerError(f"failed to initialize tokenizer OOV scorer {resolved_name!r}: {exc}") from exc
        return ProxyOovScorer(
            source="proxy_fallback",
            metadata={"requested_tokenizer": resolved_name, "fallback_reason": str(exc)},
        )


def _load_tokenizer(tokenizer_name: str) -> Any:
    try:
        from tokenizers import Tokenizer  # type: ignore[import-not-found]
    except Exception as exc:  # pragma: no cover - optional dependency path
        raise OovScorerError(
            "optional tokenizer scoring requires: pip install 'agent-lexicon[oov]'"
        ) from exc
    try:
        return Tokenizer.from_pretrained(tokenizer_name)
    except Exception as exc:  # pragma: no cover - optional dependency path
        raise OovScorerError(f"failed to load tokenizer {tokenizer_name!r}: {exc}") from exc


def _tokenize(tokenizer: Any, surface: str) -> tuple[str, ...]:
    try:
        encoded = tokenizer.encode(surface)
    except Exception as exc:
        raise OovScorerError(f"tokenizer failed to encode surface {surface!r}: {exc}") from exc
    tokens: Any = getattr(encoded, "tokens", None)
    if callable(tokens):
        tokens = tokens()
    if tokens is None and isinstance(encoded, Sequence) and not isinstance(encoded, (str, bytes)):
        tokens = encoded
    if tokens is None:
        raise OovScorerError("tokenizer encode result does not expose tokens")
    return tuple(str(token) for token in tokens)


def _is_continuation_token(token: str) -> bool:
    return token.startswith("##") or token.startswith("@@") or (len(token) == 1 and not token.isalnum())


def _is_unknown_token(token: str) -> bool:
    lowered = token.casefold()
    return lowered in {"[unk]", "<unk>", "unk"}


def _clean_text(value: str, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise OovScorerError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise OovScorerError(f"{field_name} must not be empty")
    return cleaned


def _bounded_float(value: float, *, field_name: str) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise OovScorerError(f"{field_name} must be numeric") from exc
    if numeric < 0.0 or numeric > 1.0:
        raise OovScorerError(f"{field_name} must be between 0 and 1")
    return round(numeric, 4)


__all__ = [
    "DEFAULT_OOV_TOKENIZER",
    "OovScorer",
    "OovScorerError",
    "OovScoreResult",
    "ProxyOovScorer",
    "TokenizerOovScorer",
    "build_oov_scorer",
]
