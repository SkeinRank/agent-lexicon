<h1 align="center">Agent Lexicon</h1>

<p align="center">
  <strong>A deterministic terminology layer for AI agents</strong><br>
  One canonical vocabulary across agents, branches, and tool calls.
</p>

<p align="center">
  <img alt="CI" src="https://img.shields.io/github/actions/workflow/status/SkeinRank/agent-lexicon/ci.yml?branch=main&label=CI">
  <img alt="PyPI" src="https://img.shields.io/pypi/v/agent-lexicon">
  <img alt="Python" src="https://img.shields.io/pypi/pyversions/agent-lexicon">
  <img alt="License" src="https://img.shields.io/github/license/SkeinRank/agent-lexicon">
</p>

<p align="center">
  <a href="#proof">Proof</a> ·
  <a href="docs/quickstart.md">Quickstart</a> ·
  <a href="docs/concepts.md">Concepts</a> ·
  <a href="#how-it-works">How it works</a>
</p>

**A deterministic terminology layer for AI agents. One shared vocabulary across every agent, branch, and tool call — with auditable drift detection at merge time.**

When many agents work a long coding session, each one quietly invents its own names. One branch writes `accessToken`, another `authToken`, a third `bearer_token` — all the same concept. By merge time the service speaks five dialects of itself. Agent Lexicon gives every agent a single canonical vocabulary to read from, resolves the words they actually use back to that canon, and flags terminology that drifted before it lands in `main`.

It is dependency-free, runs locally, and is deterministic by design: the same input always produces the same output, and every decision carries a reason you can audit.

```bash
pip install agent-lexicon
```

Requires Python 3.10+. Apache 2.0. Zero runtime dependencies.

---

## Proof

Real output from the shipped example lexicon (`examples/customer_limits/lexicon.yaml`). Two terms share the surface word *limit* — `billing.credit_limit` and `api.rate_limit`. This is exactly where an agent drifts and calls the wrong tool.

**An ambiguous word stops the agent instead of guessing:**

```console
$ agent-lexicon resolve examples/customer_limits/lexicon.yaml "please raise the limit"
Status: ambiguous
Action: ask_clarification
Message: Found 2 possible canonical terms.
Lexicon snapshot: sha256:98b7c5324a20c58926ea8e3413f87851c6d8c354e93197a69c56f1e142ea962e
Candidates:
- api.rate_limit (rate limit) scopes=api matches='limit'
- billing.credit_limit (credit limit) scopes=billing matches='limit'
```

**The same word, scoped, resolves cleanly:**

```console
$ agent-lexicon resolve examples/customer_limits/lexicon.yaml "please raise the limit" --scope billing
Status: resolved
Action: use_terms
Message: Resolved to billing.credit_limit.
Lexicon snapshot: sha256:98b7c5324a20c58926ea8e3413f87851c6d8c354e93197a69c56f1e142ea962e
Candidates:
- billing.credit_limit (credit limit) scopes=billing matches='limit'
```

**A wrong tool call is blocked before it runs:**

```console
$ agent-lexicon guard examples/customer_limits/lexicon.yaml "raise the credit limit" --tool api.update_rate_limit
Status: blocked
Action: block
Allowed: no
Reason: Requested tool is not allowed for the resolved terminology.
Resolution: resolved
Lexicon snapshot: sha256:98b7c5324a20c58926ea8e3413f87851c6d8c354e93197a69c56f1e142ea962e
Matched terms:
- billing.credit_limit
Allowed tools:
- billing.update_credit_limit
```

No model was called. No embedding was computed. Run it again and you get the same answer, byte for byte.

---

## Why this exists

The longer and wider an agent session runs, the more the shared vocabulary drifts. This is not a hallucination problem — the agents are not inventing facts. They are naming the *same* concept inconsistently, in branches that never see each other until merge. The result is a codebase where one idea lives under several names, and nobody decided that on purpose.

Existing tools do not close this gap:

- **Knowledge graphs** model how concepts relate, but require pre-built structure and do not gate a tool call at runtime on raw text.
- **LLM or embedding similarity** can guess that two names mean the same thing, but the guess is non-deterministic and cannot be reproduced or audited a year later.
- **Linters** catch inconsistent identifiers in code, but have no notion of a canonical term and do not work on prose, comments, or tool-call text.

Agent Lexicon is a different layer: it takes raw text in, normalizes it, resolves it against a reviewed canonical vocabulary, and returns a structured, deterministic decision. Optional semantics sit on top — as a *suggestion to a human*, never as the thing that decides.

---

## How it works

Three pieces, each doing one job.

**Resolve** — Given a span of text, find the canonical terms and aliases inside it. Matching uses a dependency-free Aho-Corasick trie, so it is fast and works on prose, comments, and code-style identifiers (`accessToken`, `access_token`, `ACCESS_TOKEN` all resolve to the same term). Input is Unicode-normalized first, so invisible separators, full-width characters, and bidi-control tricks cannot slip a different term past the matcher.

**Guard** — Given resolved terminology and a tool the agent wants to call, decide whether that call is allowed. Ambiguous terminology returns `ask_clarification`. A tool that is not permitted for the resolved term returns `block`. Bidi-control characters in the triggering text are surfaced as a high-risk finding and block by default.

**Drift detection at merge** — Read the added lines between two git refs and classify every identifier: already known, a likely alias of an existing term, or a genuinely new term that nobody reviewed. The dangerous class — a coined name with no canonical neighbour — is what surfaces by default.

```console
$ agent-lexicon check-merge --root . --base main --head feature-branch --include 'src/**'
Git merge terminology check: 1 files, 6 added lines
Range: main...feature-branch
Lexicon: lexicon/lexicon.yaml
Lexicon snapshot: sha256:98b7c5324a20c58926ea8e3413f87851c6d8c354e93197a69c56f1e142ea962e
Summary: known=2, likely_alias=0, likely_new_term=3, unresolved_unknown=0, hidden_unresolved=1
Known terminology:
- auth.py:2 'authToken' -> auth.access_token (access token) scopes=auth
New terminology candidates:
- auth.py:3 'credentialBlob' unknown; possible new term
- auth.py:4 'sessionKey' unknown; possible new term
- auth.py:5 'quuxHandle' unknown; possible new term
Hidden unresolved identifiers: 1. Use --include-unresolved-unknowns to inspect low-signal identifiers.
```

Add `--fail-on-review` to make this a blocking CI check that returns a non-zero exit code when unreviewed drift appears.

---

## Three ways to use it

**Command line** — the full local loop, no code required.

```bash
agent-lexicon init                      # create lexicon/, workspace, policy, and scan config
agent-lexicon scan                      # discover candidate terms from configured paths
agent-lexicon scan README.md docs src   # or override paths explicitly
agent-lexicon review                    # open the local web inbox to accept/reject
agent-lexicon publish                   # publish accepted decisions as a snapshot
agent-lexicon resolve <lexicon> "text"  # resolve terminology in any text
agent-lexicon guard   <lexicon> "text" --tool <name>   # gate a tool call
agent-lexicon check-merge --base main --head <branch>  # detect drift at merge
```

**Python library** — call the same logic inline.

```python
from agent_lexicon import load_lexicon, resolve_text, guard_tool_call

lexicon = load_lexicon("lexicon/lexicon.yaml")

decision = resolve_text(lexicon, "please raise the limit", scopes=["billing"])
print(decision.status)        # ResolutionStatus.RESOLVED
print(decision.action)        # ResolutionAction.USE_TERMS

guard = guard_tool_call(
    lexicon,
    "raise the credit limit",
    tool_name="api.update_rate_limit",
)
print(guard.status)           # ToolGuardStatus.BLOCKED
```

**MCP server** — expose the lexicon to any MCP-compatible agent over stdio.

```bash
agent-lexicon mcp serve --root . --lexicon lexicon/lexicon.yaml
```

The server exposes six tools: `resolve_term`, `check_language`, `guard_tool_call`, `find_evidence`, `submit_proposal`, and `get_snapshot`. List their full definitions with `agent-lexicon mcp tools`.

### Repository scan config

`agent-lexicon init` creates `.agent-lexicon/config.yaml` so common repository scans do not need long CLI commands. By default, `agent-lexicon scan` reads `README.md`, `docs`, and `src`, with include/exclude rules for docs and source files.

```yaml
scan:
  paths:
    - README.md
    - docs
    - src
  include:
    - "docs/**/*.md"
    - "src/**/*.py"
  exclude:
    - ".venv/**"
    - "node_modules/**"
    - "dist/**"
  max_file_bytes: 1000000
```

CLI flags still win when you need a one-off run:

```bash
agent-lexicon scan docs src --include "src/**/*.py" --exclude "src/generated/**"
agent-lexicon check-merge --base main --head HEAD --exclude "docs/generated/**"
```

---

## The dictionary is code

The canonical vocabulary lives in a git-tracked YAML file. A term has a canonical form, aliases, the scopes it belongs to, and optionally the tools that are allowed to act on it.

```yaml
version: 1
scopes:
  - id: billing
    label: Billing
  - id: api
    label: API
terms:
  - id: billing.credit_limit
    canonical: credit limit
    scopes: [billing]
    tools: [billing.update_credit_limit]
    aliases:
      - surface: customer cap
      - surface: account limit
  - id: api.rate_limit
    canonical: rate limit
    scopes: [api]
    tools: [api.update_rate_limit]
    aliases:
      - surface: requests per minute
```

Because it is just a file in the repo, the vocabulary versions, diffs, and reviews the same way your code does. Runtime decisions also carry a content-addressed snapshot reference (`sha256:<digest>`), so the same text can be replayed later against the exact same vocabulary content. A built-in linter warns when a surface is broad enough to over-trigger or to affect a guard decision:

```console
$ agent-lexicon lint lexicon/lexicon.yaml
Lexicon lint: warnings (1 warning)
[warning] tool_broad_surface: tool-routed term uses a broad surface that can
  affect guard decisions (term=data.primary_key; surface='PK'). Hint: Use
  explicit tool-facing aliases and avoid bare words on terms with tools.
```

---

## Optional semantics, kept honest

When the deterministic heuristics are confident, they decide alone. When a new identifier lands in a gray zone — close to an existing term but not a clear match — an optional semantic reranker can suggest the most likely canonical neighbour, so a reviewer sees *"`authToken` might be your `access token`"* instead of an unsorted pile of unknowns.

```bash
pip install "agent-lexicon[oov]"        # tokenizer-backed out-of-vocabulary scoring
pip install "agent-lexicon[semantic]"   # semantic near-miss reranking
```

This is deliberately a **suggestion to a human, marked as non-deterministic**, never an autonomous decision. The semantic layer never commits a term on its own. The thing that decides stays deterministic and auditable; the thing that suggests is allowed to be smart. That boundary is the point — it is what keeps every committed decision reproducible.

---

## Design guarantees

These hold on the deterministic runtime and local review paths:

- **Deterministic.** The same text against the same immutable lexicon snapshot always produces the same decision. No model, no embedding, no randomness on the resolve and guard paths.
- **Reproducible.** Runtime and merge reports include a content-addressed `lexicon_snapshot_ref` (`sha256:<digest>`), so a decision can be replayed later against the exact same vocabulary content.
- **Auditable.** Every decision reports its reason — which surface matched, at which span, in which scope, and why a tool was allowed or blocked.
- **Dependency-free core.** The resolver and matcher have zero runtime dependencies and run entirely in memory. Optional extras are opt-in and never touch the hot path.
- **Safe by construction.** Local writes are atomic (a reader sees a complete file or none), and the workspace database is configured for concurrent access without torn reads.

---

## Documentation

- [Quickstart](docs/quickstart.md) — local setup, scan, review, publish, and runtime usage.
- [Concepts](docs/concepts.md) — terms, aliases, scopes, resolution, guard decisions, and merge-time drift detection.

---

## Status

Agent Lexicon is an early, actively developed project (0.6.x). The core — resolve, guard, near-miss, dictionary-as-code, and merge-time drift detection — is well tested (272 passing tests) and used through the CLI, the Python API, and the local MCP server. Scaling it across many processes or a networked deployment is on the roadmap, not yet proven in production.

If terminology consistency across long, multi-agent sessions is a real cost for you — especially in regulated domains where decisions must be reproducible and auditable — this is built for exactly that.

---

## License

Apache 2.0. Free for commercial use.
