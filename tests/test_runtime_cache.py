from __future__ import annotations

import time
from pathlib import Path

from agent_lexicon import (
    Lexicon,
    LexiconRuntimeCache,
    Term,
    clear_runtime_cache,
    fingerprint_lexicon,
    get_cached_resolver,
    get_cached_surface_matcher,
    get_cached_tool_guard,
    guard_tool_call,
    load_cached_lexicon,
    resolve_text,
    runtime_cache_stats,
)
from agent_lexicon import McpServerConfig, call_mcp_tool


def test_fingerprint_is_stable_for_equal_lexicons() -> None:
    left = Lexicon(terms=(Term(id="auth.access_token", canonical="access token"),))
    right = Lexicon(terms=(Term(id="auth.access_token", canonical="access token"),))

    left_fingerprint = fingerprint_lexicon(left)
    right_fingerprint = fingerprint_lexicon(right)

    assert left_fingerprint.value == right_fingerprint.value
    assert left_fingerprint.algorithm == "sha256"
    assert left_fingerprint.term_count == 1
    assert left_fingerprint.surface_count == 1
    assert left_fingerprint.to_dict()["value"] == left_fingerprint.value


def test_cached_matcher_and_resolver_reuse_compiled_instances() -> None:
    lexicon = Lexicon(terms=(Term(id="auth.access_token", canonical="access token"),))
    cache = LexiconRuntimeCache(max_size=4)

    matcher_one = get_cached_surface_matcher(lexicon, cache=cache)
    matcher_two = get_cached_surface_matcher(lexicon, cache=cache)
    resolver_one = get_cached_resolver(lexicon, cache=cache)
    resolver_two = get_cached_resolver(lexicon, cache=cache)

    assert matcher_one is matcher_two
    assert resolver_one is resolver_two
    stats = cache.stats()
    assert stats.matcher_misses == 1
    assert stats.matcher_hits >= 1
    assert stats.resolver_misses == 1
    assert stats.resolver_hits == 1


def test_cache_key_separates_deprecated_option() -> None:
    lexicon = Lexicon(terms=(Term(id="auth.access_token", canonical="access token"),))
    cache = LexiconRuntimeCache(max_size=4)

    with_deprecated = get_cached_resolver(lexicon, include_deprecated=True, cache=cache)
    without_deprecated = get_cached_resolver(lexicon, include_deprecated=False, cache=cache)

    assert with_deprecated is not without_deprecated
    assert cache.stats().resolver_misses == 2


def test_resolve_text_uses_default_runtime_cache() -> None:
    clear_runtime_cache()
    lexicon = Lexicon(terms=(Term(id="auth.access_token", canonical="access token"),))

    first = resolve_text(lexicon, "rotate accessToken")
    second = resolve_text(lexicon, "rotate accessToken")

    assert first.primary_term_id == "auth.access_token"
    assert second.primary_term_id == "auth.access_token"
    stats = runtime_cache_stats()
    assert stats.resolver_misses == 1
    assert stats.resolver_hits == 1
    clear_runtime_cache()


def test_guard_tool_call_uses_default_runtime_cache() -> None:
    clear_runtime_cache()
    lexicon = Lexicon(terms=(Term(id="auth.access_token", canonical="access token"),))

    first = guard_tool_call(lexicon, "rotate access token", tool_name="auth.rotate")
    second = guard_tool_call(lexicon, "rotate access token", tool_name="auth.rotate")

    assert first.is_allowed is True
    assert second.is_allowed is True
    stats = runtime_cache_stats()
    assert stats.tool_guard_misses == 1
    assert stats.tool_guard_hits == 1
    clear_runtime_cache()


def test_load_cached_lexicon_reuses_file_until_file_changes(tmp_path: Path) -> None:
    path = tmp_path / "lexicon.yaml"
    path.write_text("version: 1\nterms:\n  - id: auth.access_token\n    canonical: access token\n", encoding="utf-8")
    cache = LexiconRuntimeCache(max_size=4)

    first = load_cached_lexicon(path, cache=cache)
    second = load_cached_lexicon(path, cache=cache)

    assert first is second
    assert cache.stats().lexicon_misses == 1
    assert cache.stats().lexicon_hits == 1

    time.sleep(0.01)
    path.write_text(
        "version: 1\nterms:\n  - id: auth.session_token\n    canonical: session token\n",
        encoding="utf-8",
    )
    third = load_cached_lexicon(path, cache=cache)

    assert third is not first
    assert third.terms[0].id == "auth.session_token"
    assert cache.stats().lexicon_misses == 2


def test_mcp_tools_reuse_loaded_lexicon_and_resolver() -> None:
    clear_runtime_cache()
    example_lexicon = Path(__file__).resolve().parents[1] / "examples" / "customer_limits" / "lexicon.yaml"
    config = McpServerConfig(root=example_lexicon.parents[0], lexicon_path=example_lexicon)

    first = call_mcp_tool("resolve_term", {"text": "increase the customer cap"}, config=config)
    second = call_mcp_tool("resolve_term", {"text": "increase the customer cap"}, config=config)

    assert first["decision"]["primary_term_id"] == "billing.credit_limit"
    assert second["decision"]["primary_term_id"] == "billing.credit_limit"
    stats = runtime_cache_stats()
    assert stats.lexicon_misses == 1
    assert stats.lexicon_hits == 1
    assert stats.resolver_misses == 1
    assert stats.resolver_hits == 1
    clear_runtime_cache()
