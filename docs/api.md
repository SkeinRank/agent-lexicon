# Python API reference

Agent Lexicon is usable as a library, not only through the CLI. This page
documents the stable public surface: the functions you call, the values they
return, and the enums those values use.

Everything below is importable directly from the top-level package:

```python
from agent_lexicon import load_lexicon, resolve_text, guard_tool_call
```

The resolve and guard paths are deterministic and dependency-free. The same
text against the same lexicon content always returns the same decision, with
the same matched spans and reasons.

---

## Loading a lexicon

### `load_lexicon(path, *, document_format=None) -> Lexicon`

Load a lexicon document from a JSON or YAML file.

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `path` | `str \| Path` | — | Path to a `.yaml`, `.yml`, or `.json` lexicon file. |
| `document_format` | `str \| None` | `None` | Force the parser (`"yaml"` or `"json"`). Inferred from the extension when omitted. |

Returns a frozen [`Lexicon`](#lexicon). Raises `AgentLexiconLoadError` on a
missing file or malformed document, and `AgentLexiconModelError` when the
document parses but violates the model (for example, an empty term id).

Related helpers:

- `loads_lexicon(text, *, document_format=None) -> Lexicon` — load from an
  in-memory string instead of a file.
- `lexicon_from_dict(data) -> Lexicon` — build directly from a parsed mapping.
- `load_cached_lexicon(path, ...)` — same as `load_lexicon`, but reuses a
  process-wide cache keyed by path and content.

```python
from agent_lexicon import load_lexicon

lexicon = load_lexicon("lexicon/lexicon.yaml")
```

---

## Resolving terminology

### `resolve_text(lexicon, text, *, ...) -> ResolutionDecision`

Find the canonical terms and aliases inside a span of text.

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `lexicon` | `Lexicon` | — | A loaded lexicon. |
| `text` | `str` | — | Raw text. Unicode-normalized before matching. |
| `scopes` | `Iterable[str] \| None` | `None` | Restrict resolution to these scopes. `None` considers all scopes and is what surfaces ambiguity. |
| `include_deprecated` | `bool` | `True` | Whether deprecated surfaces still match. |
| `include_near_misses` | `bool` | `True` | Attach non-deterministic near-miss suggestions for gray-zone identifiers. |
| `near_miss_max_suggestions` | `int` | `3` | Cap on suggestions returned. |
| `near_miss_min_confidence` | `float` | `0.42` | Minimum confidence for a near-miss to be surfaced. |
| `near_miss_semantic_backend` | `SemanticNearMissBackend \| None` | `None` | Optional semantic reranker. Suggestion-only; never decides. |
| `use_cache` | `bool` | `True` | Reuse a process-wide compiled resolver. Set `False` for an isolated resolver. |

Returns a [`ResolutionDecision`](#resolutiondecision).

```python
decision = resolve_text(lexicon, "please raise the limit", scopes=["billing"])
print(decision.status)   # ResolutionStatus.RESOLVED
print(decision.action)   # ResolutionAction.USE_TERMS
print(decision.candidates[0].term_id)   # "billing.credit_limit"
```

For an isolated resolver you control directly, construct
`LexiconResolver.from_lexicon(lexicon)` and call `.resolve(text, ...)`; the
keyword arguments match `resolve_text` minus `use_cache`.

---

## Guarding a tool call

### `guard_tool_call(lexicon, text, *, tool_name, ...) -> ToolGuardDecision`

Decide whether a tool an agent wants to call is allowed for the terminology in
the triggering text.

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `lexicon` | `Lexicon` | — | A loaded lexicon. |
| `text` | `str` | — | Text that triggered the tool call. |
| `tool_name` | `str` | — | Requested tool (keyword-only). |
| `scopes` | `Iterable[str] \| None` | `None` | Restrict resolution to these scopes. |
| `include_deprecated` | `bool` | `True` | Whether deprecated surfaces still match. |
| `block_on_unicode_risk` | `bool` | `True` | Block when the triggering text carries high-risk Unicode (e.g. bidi-control characters). |
| `use_cache` | `bool` | `True` | Reuse a process-wide compiled guard. |

Returns a [`ToolGuardDecision`](#toolguarddecision).

```python
guard = guard_tool_call(
    lexicon,
    "raise the credit limit",
    tool_name="api.update_rate_limit",
)
print(guard.status)   # ToolGuardStatus.BLOCKED
print(guard.reason)   # human-readable explanation
```

---

## Return values

### `ResolutionDecision`

Frozen dataclass returned by `resolve_text`.

| Field | Type | Notes |
|---|---|---|
| `text` | `str` | The input text. |
| `status` | [`ResolutionStatus`](#enums) | `RESOLVED`, `AMBIGUOUS`, or `UNKNOWN`. |
| `action` | [`ResolutionAction`](#enums) | Recommended next action. |
| `candidates` | `tuple[ResolutionCandidate, ...]` | Matched canonical terms. |
| `matches` | `tuple[ResolutionMatch, ...]` | Individual surface occurrences with spans. |
| `message` | `str \| None` | Human-readable summary. |
| `metadata` | `Mapping[str, Any]` | Includes the `lexicon_snapshot_ref` and Unicode findings. |

### `ResolutionCandidate`

| Field | Type | Notes |
|---|---|---|
| `term_id` | `str` | Canonical term id, e.g. `billing.credit_limit`. |
| `canonical` | `str` | Canonical surface form. |
| `description` | `str \| None` | Optional description. |
| `scopes` | `tuple[str, ...]` | Scopes the term belongs to. |
| `tags` | `tuple[str, ...]` | Free-form tags. |
| `matched_surfaces` | `tuple[str, ...]` | Which surfaces triggered the match. |
| `match_count` | `int` | Number of surface occurrences. |
| `evidence_count` | `int` | Attached evidence spans. |
| `deprecated` | `bool` | Whether the term is deprecated. |
| `score` | `float` | Ranking score. |
| `metadata` | `Mapping[str, Any]` | Extra data. |

### `ResolutionMatch`

| Field | Type | Notes |
|---|---|---|
| `term_id` | `str` | Term the surface belongs to. |
| `surface` | `str` | Canonical surface that matched. |
| `matched_text` | `str` | The literal text as it appeared. |
| `start` / `end` | `int` | Character span in the normalized text. |
| `kind` | `str` | Match kind (e.g. canonical, alias, identifier). |
| `scopes` | `tuple[str, ...]` | Scopes of the matched term. |
| `deprecated` | `bool` | Whether the surface is deprecated. |

### `ToolGuardDecision`

Frozen dataclass returned by `guard_tool_call`.

| Field | Type | Notes |
|---|---|---|
| `text` | `str` | Triggering text. |
| `tool_name` | `str` | Requested tool. |
| `status` | [`ToolGuardStatus`](#enums) | `ALLOWED`, `BLOCKED`, `NEEDS_CLARIFICATION`, or `NO_MATCH`. |
| `action` | [`ToolGuardAction`](#enums) | `PROCEED`, `BLOCK`, or `ASK_CLARIFICATION`. |
| `resolution` | `ResolutionDecision` | The underlying resolution. |
| `reason` | `str` | Why the call was allowed or blocked. |
| `allowed_tool_names` | `tuple[str, ...]` | Tools permitted for the matched terms. |
| `matched_term_ids` | `tuple[str, ...]` | Terms the text resolved to. |
| `metadata` | `Mapping[str, Any]` | Includes Unicode risk findings. |

---

## Enums

All enums are string enums, so their `.value` is a stable lowercase string
safe to serialize.

### `ResolutionStatus`

| Member | Value | Meaning |
|---|---|---|
| `RESOLVED` | `"resolved"` | Text points to exactly one canonical term. |
| `AMBIGUOUS` | `"ambiguous"` | Text could mean more than one term. |
| `UNKNOWN` | `"unknown"` | No known terminology found. |

### `ResolutionAction`

| Member | Value | Meaning |
|---|---|---|
| `USE_TERMS` | `"use_terms"` | Proceed with the resolved terms. |
| `ASK_CLARIFICATION` | `"ask_clarification"` | Ambiguous; ask before acting. |
| `NO_MATCH` | `"no_match"` | Nothing matched. |

### `ToolGuardStatus`

| Member | Value | Meaning |
|---|---|---|
| `ALLOWED` | `"allowed"` | Tool is permitted for the resolved term. |
| `BLOCKED` | `"blocked"` | Tool is not permitted, or blocked on Unicode risk. |
| `NEEDS_CLARIFICATION` | `"needs_clarification"` | Terminology is ambiguous. |
| `NO_MATCH` | `"no_match"` | No terminology matched the text. |

### `ToolGuardAction`

| Member | Value | Meaning |
|---|---|---|
| `PROCEED` | `"proceed"` | Safe to call the tool. |
| `BLOCK` | `"block"` | Do not call the tool. |
| `ASK_CLARIFICATION` | `"ask_clarification"` | Resolve ambiguity first. |

---

## The `Lexicon` model

`load_lexicon` returns a frozen `Lexicon`:

| Field | Type | Notes |
|---|---|---|
| `version` | `str` | Document version, default `"1"`. |
| `scopes` | `tuple[Scope, ...]` | Declared namespaces. |
| `terms` | `tuple[Term, ...]` | Canonical terms. |
| `proposals` | `tuple[ProposalCandidate, ...]` | Pending proposals, if any. |
| `metadata` | `Mapping[str, Any]` | Document-level metadata. |

A `Term` has `id`, `canonical`, optional `description`, `aliases`, `scopes`,
`tags`, `tools` (allowed tool names for guarding), `deprecated`, and
`evidence`. A `Scope` has `id`, optional `label`, `description`, and `parents`.

See [Concepts](concepts.md) for how these fit together, and the README's
*The dictionary is code* section for the on-disk YAML shape.

---

## Snapshot references

Every runtime decision carries a content-addressed reference to the exact
lexicon content it used, under `metadata["lexicon_snapshot_ref"]`
(`sha256:<digest>`). Compute it directly with:

```python
from agent_lexicon import lexicon_snapshot_ref

ref = lexicon_snapshot_ref(lexicon)   # "sha256:98b7c5..."
```

This is what makes a decision replayable: the guarantee is not "whatever
`lexicon.yaml` contains today", but "this text was resolved against this exact
lexicon content".
