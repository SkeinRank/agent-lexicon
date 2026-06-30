"""In-process runtime cache for compiled Agent Lexicon objects.

The runtime cache keeps loaded lexicons, compiled surface matchers, resolvers,
and tool guards in memory for long-running local processes such as MCP servers,
web inboxes, and agent runners. It is deliberately dependency-free and keyed by
stable lexicon fingerprints, so cache hits never change matching semantics.
"""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any

from .matcher import SurfaceMatcher, build_surface_matcher
from .models import Lexicon
from .resolver import LexiconResolver

DEFAULT_RUNTIME_CACHE_SIZE = 16
_CACHE_ALGORITHM = "sha256"


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

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible fingerprint payload."""
        return {
            "algorithm": self.algorithm,
            "value": self.value,
            "version": self.version,
            "term_count": self.term_count,
            "scope_count": self.scope_count,
            "proposal_count": self.proposal_count,
            "surface_count": self.surface_count,
        }


@dataclass(frozen=True, slots=True)
class RuntimeCacheStats:
    """Counters and sizes for the in-process runtime cache."""

    max_size: int
    lexicon_hits: int = 0
    lexicon_misses: int = 0
    matcher_hits: int = 0
    matcher_misses: int = 0
    resolver_hits: int = 0
    resolver_misses: int = 0
    tool_guard_hits: int = 0
    tool_guard_misses: int = 0
    lexicon_entries: int = 0
    matcher_entries: int = 0
    resolver_entries: int = 0
    tool_guard_entries: int = 0

    def to_dict(self) -> dict[str, int]:
        """Return a JSON-compatible stats payload."""
        return {
            "max_size": self.max_size,
            "lexicon_hits": self.lexicon_hits,
            "lexicon_misses": self.lexicon_misses,
            "matcher_hits": self.matcher_hits,
            "matcher_misses": self.matcher_misses,
            "resolver_hits": self.resolver_hits,
            "resolver_misses": self.resolver_misses,
            "tool_guard_hits": self.tool_guard_hits,
            "tool_guard_misses": self.tool_guard_misses,
            "lexicon_entries": self.lexicon_entries,
            "matcher_entries": self.matcher_entries,
            "resolver_entries": self.resolver_entries,
            "tool_guard_entries": self.tool_guard_entries,
        }


class LexiconRuntimeCache:
    """Small thread-safe LRU cache for compiled runtime objects."""

    def __init__(self, *, max_size: int = DEFAULT_RUNTIME_CACHE_SIZE) -> None:
        if max_size < 1:
            raise ValueError("max_size must be greater than 0")
        self.max_size = max_size
        self._lock = RLock()
        self._lexicons: OrderedDict[tuple[object, ...], Lexicon] = OrderedDict()
        self._matchers: OrderedDict[tuple[object, ...], SurfaceMatcher] = OrderedDict()
        self._resolvers: OrderedDict[tuple[object, ...], LexiconResolver] = OrderedDict()
        self._tool_guards: OrderedDict[tuple[object, ...], object] = OrderedDict()
        self._lexicon_hits = 0
        self._lexicon_misses = 0
        self._matcher_hits = 0
        self._matcher_misses = 0
        self._resolver_hits = 0
        self._resolver_misses = 0
        self._tool_guard_hits = 0
        self._tool_guard_misses = 0

    def load_lexicon(self, path: str | Path, *, document_format: str | None = None) -> Lexicon:
        """Load a lexicon file with mtime/size-based invalidation."""
        source_path = Path(path)
        try:
            stat = source_path.stat()
        except OSError:
            from .loader import load_lexicon

            return load_lexicon(source_path, document_format=document_format)
        key = (
            "lexicon-file",
            str(source_path.resolve()),
            document_format,
            stat.st_mtime_ns,
            stat.st_size,
        )
        with self._lock:
            cached = self._get_locked(self._lexicons, key)
            if cached is not None:
                self._lexicon_hits += 1
                return cached
            self._lexicon_misses += 1

        from .loader import load_lexicon

        lexicon = load_lexicon(source_path, document_format=document_format)
        with self._lock:
            self._put_locked(self._lexicons, key, lexicon)
        return lexicon

    def get_matcher(self, lexicon: Lexicon, *, include_deprecated: bool = True) -> SurfaceMatcher:
        """Return a cached compiled matcher for a lexicon/options pair."""
        fingerprint = fingerprint_lexicon(lexicon)
        key = ("matcher", fingerprint.value, include_deprecated)
        with self._lock:
            cached = self._get_locked(self._matchers, key)
            if cached is not None:
                self._matcher_hits += 1
                return cached
            self._matcher_misses += 1

        matcher = build_surface_matcher(lexicon, include_deprecated=include_deprecated)
        with self._lock:
            self._put_locked(self._matchers, key, matcher)
        return matcher

    def get_resolver(self, lexicon: Lexicon, *, include_deprecated: bool = True) -> LexiconResolver:
        """Return a cached resolver for a lexicon/options pair."""
        fingerprint = fingerprint_lexicon(lexicon)
        key = ("resolver", fingerprint.value, include_deprecated)
        with self._lock:
            cached = self._get_locked(self._resolvers, key)
            if cached is not None:
                self._resolver_hits += 1
                return cached
            self._resolver_misses += 1

        resolver = LexiconResolver(lexicon=lexicon, matcher=self.get_matcher(lexicon, include_deprecated=include_deprecated))
        with self._lock:
            self._put_locked(self._resolvers, key, resolver)
        return resolver

    def get_tool_guard(self, lexicon: Lexicon, *, include_deprecated: bool = True):
        """Return a cached tool guard for a lexicon/options pair."""
        fingerprint = fingerprint_lexicon(lexicon)
        key = ("tool-guard", fingerprint.value, include_deprecated)
        with self._lock:
            cached = self._get_locked(self._tool_guards, key)
            if cached is not None:
                self._tool_guard_hits += 1
                return cached
            self._tool_guard_misses += 1

        from .tool_guard import ToolGuard

        guard = ToolGuard(
            lexicon,
            include_deprecated=include_deprecated,
            resolver=self.get_resolver(lexicon, include_deprecated=include_deprecated),
        )
        with self._lock:
            self._put_locked(self._tool_guards, key, guard)
        return guard

    def clear(self) -> None:
        """Clear cached objects and reset counters."""
        with self._lock:
            self._lexicons.clear()
            self._matchers.clear()
            self._resolvers.clear()
            self._tool_guards.clear()
            self._lexicon_hits = 0
            self._lexicon_misses = 0
            self._matcher_hits = 0
            self._matcher_misses = 0
            self._resolver_hits = 0
            self._resolver_misses = 0
            self._tool_guard_hits = 0
            self._tool_guard_misses = 0

    def stats(self) -> RuntimeCacheStats:
        """Return current cache counters and entry counts."""
        with self._lock:
            return RuntimeCacheStats(
                max_size=self.max_size,
                lexicon_hits=self._lexicon_hits,
                lexicon_misses=self._lexicon_misses,
                matcher_hits=self._matcher_hits,
                matcher_misses=self._matcher_misses,
                resolver_hits=self._resolver_hits,
                resolver_misses=self._resolver_misses,
                tool_guard_hits=self._tool_guard_hits,
                tool_guard_misses=self._tool_guard_misses,
                lexicon_entries=len(self._lexicons),
                matcher_entries=len(self._matchers),
                resolver_entries=len(self._resolvers),
                tool_guard_entries=len(self._tool_guards),
            )

    def _get_locked(self, store: OrderedDict[tuple[object, ...], Any], key: tuple[object, ...]) -> Any | None:
        value = store.get(key)
        if value is None:
            return None
        store.move_to_end(key)
        return value

    def _put_locked(self, store: OrderedDict[tuple[object, ...], Any], key: tuple[object, ...], value: Any) -> None:
        store[key] = value
        store.move_to_end(key)
        while len(store) > self.max_size:
            store.popitem(last=False)


def fingerprint_lexicon(lexicon: Lexicon) -> LexiconFingerprint:
    """Return a stable fingerprint for a loaded lexicon."""
    payload = json.dumps(
        lexicon.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return LexiconFingerprint(
        algorithm=_CACHE_ALGORITHM,
        value=digest,
        version=lexicon.version,
        term_count=len(lexicon.terms),
        scope_count=len(lexicon.scopes),
        proposal_count=len(lexicon.proposals),
        surface_count=sum(len(term.surfaces(include_deprecated=True)) for term in lexicon.terms),
    )


def load_cached_lexicon(
    path: str | Path,
    *,
    document_format: str | None = None,
    cache: LexiconRuntimeCache | None = None,
) -> Lexicon:
    """Load a lexicon through the shared in-process runtime cache."""
    active_cache = cache if cache is not None else default_runtime_cache()
    return active_cache.load_lexicon(path, document_format=document_format)


def get_cached_surface_matcher(
    lexicon: Lexicon,
    *,
    include_deprecated: bool = True,
    cache: LexiconRuntimeCache | None = None,
) -> SurfaceMatcher:
    """Return a cached compiled surface matcher."""
    active_cache = cache if cache is not None else default_runtime_cache()
    return active_cache.get_matcher(lexicon, include_deprecated=include_deprecated)


def get_cached_resolver(
    lexicon: Lexicon,
    *,
    include_deprecated: bool = True,
    cache: LexiconRuntimeCache | None = None,
) -> LexiconResolver:
    """Return a cached resolver."""
    active_cache = cache if cache is not None else default_runtime_cache()
    return active_cache.get_resolver(lexicon, include_deprecated=include_deprecated)


def get_cached_tool_guard(
    lexicon: Lexicon,
    *,
    include_deprecated: bool = True,
    cache: LexiconRuntimeCache | None = None,
):
    """Return a cached tool guard."""
    active_cache = cache if cache is not None else default_runtime_cache()
    return active_cache.get_tool_guard(lexicon, include_deprecated=include_deprecated)


def default_runtime_cache() -> LexiconRuntimeCache:
    """Return the process-wide runtime cache."""
    return _DEFAULT_RUNTIME_CACHE


def clear_runtime_cache() -> None:
    """Clear the process-wide runtime cache."""
    _DEFAULT_RUNTIME_CACHE.clear()


def runtime_cache_stats() -> RuntimeCacheStats:
    """Return process-wide runtime cache stats."""
    return _DEFAULT_RUNTIME_CACHE.stats()


_DEFAULT_RUNTIME_CACHE = LexiconRuntimeCache()


__all__ = [
    "DEFAULT_RUNTIME_CACHE_SIZE",
    "LexiconFingerprint",
    "LexiconRuntimeCache",
    "RuntimeCacheStats",
    "clear_runtime_cache",
    "default_runtime_cache",
    "fingerprint_lexicon",
    "get_cached_resolver",
    "get_cached_surface_matcher",
    "get_cached_tool_guard",
    "load_cached_lexicon",
    "runtime_cache_stats",
]
