# Concepts

A short tour of the ideas behind Agent Lexicon.

## Terms, aliases, and scopes

A **term** is a single canonical concept. It has a canonical form (`credit limit`), the **scopes** it belongs to (`billing`), and any number of **aliases** — other surfaces that mean the same thing (`customer cap`, `account limit`).

A **scope** is a namespace. The same word can mean different things in different scopes: `limit` under `billing` is a credit limit; under `api` it is a rate limit. Scopes are how the same surface resolves to different terms depending on context.

## Resolution

**Resolution** takes raw text and finds the terms inside it. The outcome is one of three states:

- **resolved** — the text points to exactly one canonical term.
- **ambiguous** — the text could mean more than one term (for example, `limit` with no scope). The agent should ask for clarification rather than guess.
- **unknown** — no known terminology was found.

Resolution is deterministic. The same text against the same immutable lexicon snapshot always returns the same state, with the same matched spans and reasons. Runtime decisions include a `lexicon_snapshot_ref` such as `sha256:<digest>` so the exact vocabulary content used for a decision can be identified later.

## Immutable lexicon snapshots

A loaded lexicon is treated as immutable runtime input. Agent Lexicon computes a stable content fingerprint for that input and exposes it as `lexicon_snapshot_ref` (`sha256:<digest>`) in resolver decisions, guard decisions, merge reports, and published snapshot metadata.

This keeps reproducibility independent from mutable files on disk: the guarantee is not "whatever `lexicon.yaml` contains today", but "this text was resolved against this exact lexicon content".

## Decision provenance log

The local workspace keeps an append-only decision provenance log. Human review decisions, snapshot publication decisions, and future deterministic policy decisions can be stored as self-contained records with actor, action, subject, input, result, rule identifier, payload, and lexicon snapshot metadata.

This is different from the current review state. The current state answers "what is the latest decision for this candidate?" The provenance log answers "what decisions were made, in what order, by whom or by which policy rule, against which vocabulary content?"

## Code-style identifiers

Agents write terminology as code, not just prose. Agent Lexicon resolves identifier forms of a known surface — `accessToken`, `access_token`, `ACCESS_TOKEN` all resolve to the term whose surface is `access token`. This is what lets drift detection see terminology inside real source, not only in comments.

## Repository scan config

Agent Lexicon treats repository files as terminology-bearing text. `.agent-lexicon/config.yaml` defines the default scan surface: which paths to read, which glob patterns to include, which paths to ignore, and the maximum file size. CLI flags can override these rules for a one-off scan, while the config keeps local runs and CI jobs consistent.

## Unicode normalization

Before matching, text is normalized: invisible separators, full-width characters, and ligatures are folded, and bidi-control characters are removed and reported. This means a term cannot be hidden from the matcher with invisible characters, and a guard decision cannot be steered to the wrong term by obfuscated text. Real accents and distinct letters are preserved — normalization makes lookalikes consistent without collapsing genuinely different words.

## Tool guarding

A term can declare which **tools** are allowed to act on it. The guard takes resolved terminology plus the tool an agent wants to call and returns:

- **proceed** — the tool is allowed, or the term has no tool restrictions.
- **ask_clarification** — the terminology is ambiguous; resolve it before acting.
- **block** — the requested tool is not permitted for the resolved term.

## Drift detection

In a long multi-agent session, branches accumulate independent naming decisions. At merge, `check-merge` reads the added lines between two git refs and sorts every identifier into:

- **known** — already a canonical term or alias.
- **likely alias** — close to an existing term; probably a new alias to approve.
- **likely new term** — a genuinely new concept nobody reviewed. This is the class that matters most: a coined name with no canonical neighbour, surfaced by default so it does not slip into `main` unnoticed.

## The deterministic / suggestive boundary

Everything that **decides** is deterministic: resolution, guarding, and the heuristic drift classification. Optional semantic reranking only **suggests** — it points a human reviewer at the most likely canonical neighbour for a gray-zone identifier, marked as non-deterministic, and never commits a decision on its own.

This boundary is deliberate. It is what lets every committed decision be reproduced and audited later, while still letting the suggestion layer be as smart as you want.
