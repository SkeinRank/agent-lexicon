# Agent Lexicon

Shared terminology memory for AI agents.

Agent Lexicon is a lightweight Python package for giving agents a small,
reviewable terminology layer before RAG, tool calls, and workflow automation.

## Install

```bash
pip install agent-lexicon
```

## Quick start

Use the product-facing commands for the common local loop:

```bash
agent-lexicon init
agent-lexicon scan README.md docs src
agent-lexicon analyze --priority important
agent-lexicon review
agent-lexicon publish
agent-lexicon mcp serve --root . --lexicon lexicon/lexicon.yaml
```

`scan` reads local project files, runs prompt-safety checks, discovers candidate
terms, builds evidence packs, computes candidate-quality signals, and saves the
result to `.agent-lexicon/`. `analyze` summarizes the highest-priority
candidates so reviewers can start with the important terminology first.

## Quick check

```bash
agent-lexicon --version
python -m agent_lexicon --version
agent-lexicon init --root /tmp/agent-lexicon-demo
agent-lexicon scan examples/customer_limits/docs --root examples/customer_limits --max-candidates 5
agent-lexicon analyze --root examples/customer_limits
agent-lexicon publish --root examples/customer_limits
agent-lexicon validate examples/customer_limits/lexicon.yaml
agent-lexicon match examples/customer_limits/lexicon.yaml "The customer cap and rate limit changed." --longest-only
agent-lexicon resolve examples/customer_limits/lexicon.yaml "increase the limit"
agent-lexicon guard examples/customer_limits/lexicon.yaml "increase the limit" --tool api.update_rate_limit
agent-lexicon validate-queries examples/customer_limits/queries.jsonl
agent-lexicon check examples/customer_limits/lexicon.yaml examples/customer_limits/queries.jsonl
agent-lexicon ingest README.md src examples/customer_limits/docs --root .
agent-lexicon discover-candidates examples/customer_limits/docs --root examples/customer_limits
agent-lexicon build-evidence examples/customer_limits/docs --root examples/customer_limits
agent-lexicon safety scan examples/customer_limits/docs --root examples/customer_limits
agent-lexicon policy status --root examples/customer_limits
agent-lexicon workspace init --root examples/customer_limits
agent-lexicon workspace sync examples/customer_limits/docs --root examples/customer_limits --max-candidates 5
agent-lexicon workspace status --root examples/customer_limits
agent-lexicon review --root examples/customer_limits
agent-lexicon workspace export-review-events --root examples/customer_limits
agent-lexicon discover-migrations examples/customer_limits/lexicon.yaml
agent-lexicon dictionary init --root .
agent-lexicon dictionary validate --root .
agent-lexicon dictionary diff lexicon/lexicon.yaml lexicon-next.yaml
agent-lexicon dictionary merge lexicon-base.yaml lexicon-ours.yaml lexicon-theirs.yaml --output lexicon-merged.json
agent-lexicon dictionary pr-check --root .
agent-lexicon review-agent assess --root examples/customer_limits
agent-lexicon review-agent consensus --root examples/customer_limits
agent-lexicon review-agent prompt --root examples/customer_limits
agent-lexicon review-agent dataset --root examples/customer_limits
agent-lexicon mcp tools
agent-lexicon mcp serve --root . --lexicon lexicon/lexicon.yaml
```

## Simple local workflow

The short commands are wrappers around the lower-level SDK and CLI surfaces.
They keep the first-run workflow small without hiding the underlying building
blocks.

```bash
agent-lexicon init
```

Creates the git-tracked `lexicon/` layout, the local SQLite workspace under
`.agent-lexicon/`, and a local policy file.

```bash
agent-lexicon scan README.md docs src
```

Reads local docs and source files, filters existing lexicon surfaces, scores
terminology candidates, builds positive/negative evidence, runs prompt-safety
checks, adds OOV-proxy and clustering metadata, and stores the result in the
local workspace.

```bash
agent-lexicon analyze --review-agent --consensus
```

Shows important candidates first and can include deterministic Review Agent
recommendations for quick triage. Use `--consensus` to show the consensus and
abstention wrapper used for safer auto-triage decisions. Use `--priority
important` to focus the inbox on surfaces that look risky, internal, clustered,
or likely to affect agent behavior.

```bash
agent-lexicon publish
```

Publishes accepted review decisions into a lexicon-compatible local snapshot.

## Candidate quality

Local Scout now attaches dependency-free quality signals to each discovered
candidate:

- `oov_proxy_score` estimates tokenizer pain from code-style shape, separators,
  camel case, acronyms, and digits.
- `token_fragmentation_score` highlights surfaces that are likely to split into
  many tokens.
- `surface_risk_score` combines shape, OOV proxy, cluster size, and negative
  evidence signals.
- `cluster_key` groups variants such as `PaymentCore`, `payment-core`, and
  `payments_core` before review.
- `priority` separates candidates into `important` and `later` review buckets.

This keeps the web inbox and `agent-lexicon analyze` focused on the candidates
that are most likely to matter for agent behavior and retrieval quality.

```bash
agent-lexicon scan README.md docs src
agent-lexicon analyze --priority important
```

## Core schema

The package includes dependency-free core models for terminology workflows:

- `Scope` — a project, team, domain, or workflow boundary where a term has a specific meaning.
- `Term` — a canonical domain term with aliases, scopes, tags, evidence, and metadata.
- `Alias` — a surface form that points to a canonical term.
- `EvidenceSpan` — a source-backed snippet with file path, line range, and evidence kind.
- `ProposalCandidate` — a reviewable terminology change suggested by local analysis or an agent.
- `Lexicon` — a validated terminology document containing scopes, terms, proposals, and metadata.

Example:

```python
from agent_lexicon import Alias, EvidenceSpan, ProposalCandidate, ProposalKind, Term

term = Term(
    id="billing.credit_limit",
    canonical="credit limit",
    aliases=(Alias(surface="customer cap", term_id="billing.credit_limit"),),
    scopes=("billing",),
)

evidence = EvidenceSpan(
    source_path="docs/billing.md",
    start_line=42,
    snippet="Customer cap is the credit limit for an account.",
)

proposal = ProposalCandidate(
    id="proposal.customer-cap.alias",
    kind=ProposalKind.ALIAS_CANDIDATE,
    surface="customer cap",
    candidate_term_id="billing.credit_limit",
    confidence=0.78,
    evidence=(evidence,),
)
```

## Lexicon documents

Agent Lexicon can load and validate JSON or YAML terminology documents. The
current document format is intentionally small and local-first:

```yaml
version: 1
scopes:
  - id: billing
    label: Billing
terms:
  - id: billing.credit_limit
    canonical: credit limit
    scopes: [billing]
    tools: [billing.update_credit_limit]
    aliases:
      - surface: customer cap
        scopes: [billing]
    evidence:
      - source_path: docs/billing.md
        start_line: 12
        snippet: Customer cap is the credit limit for an account.
        kind: positive
```

Validate a document from the command line:

```bash
agent-lexicon validate examples/customer_limits/lexicon.yaml
agent-lexicon match examples/customer_limits/lexicon.yaml "The customer cap and rate limit changed." --longest-only
agent-lexicon resolve examples/customer_limits/lexicon.yaml "increase the limit"
agent-lexicon guard examples/customer_limits/lexicon.yaml "increase the limit" --tool api.update_rate_limit
```

Load the same document from Python:

```python
from agent_lexicon import Lexicon, load_lexicon

lexicon = load_lexicon("examples/customer_limits/lexicon.yaml")
assert lexicon.get_term("billing.credit_limit") is not None

lexicon_again = Lexicon.from_file("examples/customer_limits/lexicon.json")
```

The loader validates duplicate ids, unknown scope references, alias collisions,
and proposal references before returning a `Lexicon` object.


## Surface matching

Agent Lexicon can scan text for canonical terms and aliases from a loaded
lexicon. The matcher is dependency-free and uses a trie with Aho-Corasick
failure links, so it can be used by runtime agents before retrieval, tool calls,
or local review workflows.

```python
from agent_lexicon import build_surface_matcher, load_lexicon

lexicon = load_lexicon("examples/customer_limits/lexicon.yaml")
matcher = build_surface_matcher(lexicon)

matches = matcher.match(
    "The customer cap and rate limit changed.",
    longest_only=True,
)

for match in matches:
    print(match.term_id, match.kind.value, match.matched_text)
```

Command line usage:

```bash
agent-lexicon match examples/customer_limits/lexicon.yaml "The customer cap and rate limit changed."
```

The matcher supports scope filtering, case-sensitive aliases, deprecated surface
filtering, and longest non-overlapping output for downstream resolver logic.


## Runtime resolution

The resolver turns surface matches into a deterministic runtime decision. It
prefers longer non-overlapping surfaces, preserves same-span ambiguity, and
returns one of three statuses: `resolved`, `ambiguous`, or `unknown`.

```python
from agent_lexicon import load_lexicon, resolve_text

lexicon = load_lexicon("examples/customer_limits/lexicon.yaml")

decision = resolve_text(lexicon, "increase the limit")
print(decision.status.value)  # ambiguous
print(decision.action.value)  # ask_clarification

billing_decision = resolve_text(
    lexicon,
    "increase the limit",
    scopes=("billing",),
)
print(billing_decision.primary_term_id)  # billing.credit_limit
```

Command line usage:

```bash
agent-lexicon resolve examples/customer_limits/lexicon.yaml "increase the limit"
agent-lexicon guard examples/customer_limits/lexicon.yaml "increase the limit" --tool api.update_rate_limit
agent-lexicon resolve examples/customer_limits/lexicon.yaml "increase the limit" --scope billing
```

This gives agents a local way to stop before unsafe assumptions: if the same
surface can mean multiple canonical terms, the recommended action is
`ask_clarification`.


## Tool-call safety

Agent Lexicon can check a requested tool call before the agent executes it. If
terminology is ambiguous, the guard asks for clarification instead of allowing a
risky tool call. If a term is resolved and declares allowed tools, the requested
tool must match that term's tool list.

```python
from agent_lexicon import guard_tool_call, load_lexicon

lexicon = load_lexicon("examples/customer_limits/lexicon.yaml")

decision = guard_tool_call(
    lexicon,
    "increase the limit",
    tool_name="api.update_rate_limit",
)

print(decision.status.value)  # needs_clarification
print(decision.action.value)  # ask_clarification
print(decision.is_allowed)    # False
```

Command line usage:

```bash
agent-lexicon guard examples/customer_limits/lexicon.yaml "increase the limit" --tool api.update_rate_limit
agent-lexicon guard examples/customer_limits/lexicon.yaml "increase the limit" --tool billing.update_credit_limit --scope billing
```

The `guard` command returns `0` for allowed or no-match decisions and `2` when
the tool call is blocked or needs clarification. This makes it usable in local
agent wrappers and future CI checks.



## Local ingest

Agent Lexicon can read local project files into deterministic text documents for
future scout, evidence, and review workflows. Directory scans use local-project
defaults: README files, `docs/`, `src/`, Markdown, JSON/YAML, TOML, and common
text/code files. Large files, binary files, virtual environments, build outputs,
and cache directories are skipped.

Command line usage:

```bash
agent-lexicon ingest README.md src examples/customer_limits/docs --root .
agent-lexicon discover-candidates examples/customer_limits/docs --root examples/customer_limits
agent-lexicon build-evidence examples/customer_limits/docs --root examples/customer_limits
agent-lexicon ingest examples/customer_limits/docs --root examples/customer_limits --jsonl
```

Python usage:

```python
from agent_lexicon import ingest_local_paths

report = ingest_local_paths(["README.md", "src", "examples/customer_limits/docs"], root=".")

for document in report.documents:
    print(document.relative_path, document.kind.value, document.line_count)
```

The ingest report exposes `document_count`, `total_lines`, `total_size_bytes`,
`documents`, and `skipped_paths`. Each document includes a stable SHA-256 hash,
relative path, source kind, line count, byte size, and text content.

## Candidate discovery

Agent Lexicon can run a deterministic local scout pass over ingested documents.
The scout discovers reviewable terminology candidates, assigns a score, reports
a jargon score, and applies background penalties so common project words do not
dominate the candidate list. This step is local-first and dependency-free.

Command line usage:

```bash
agent-lexicon discover-candidates examples/customer_limits/docs --root examples/customer_limits
agent-lexicon discover-candidates examples/customer_limits/docs --root examples/customer_limits --lexicon examples/customer_limits/lexicon.yaml --json
```

Python usage:

```python
from agent_lexicon import discover_scout_candidates, ingest_local_paths

ingest_report = ingest_local_paths(["examples/customer_limits/docs"], root="examples/customer_limits")
candidate_report = discover_scout_candidates(ingest_report.documents)

for candidate in candidate_report.candidates:
    print(candidate.surface, candidate.score, candidate.jargon_score, candidate.background_penalty)
```

Each candidate includes a surface, normalized surface, kind, score, jargon
score, background penalty, occurrence count, document count, source occurrences,
and a deterministic score breakdown. Existing lexicon surfaces can be filtered
out with `existing_surfaces_from_lexicon(...)` or the CLI `--lexicon` option.

## Evidence packs

Evidence packs turn discovered candidates into reviewable snippets with file
paths and line numbers. Positive snippets show exact candidate occurrences.
Negative snippets show partial token overlap without the exact surface, which
helps reviewers spot broad, overloaded, or weak terminology candidates.

Command line usage:

```bash
agent-lexicon build-evidence examples/customer_limits/docs --root examples/customer_limits
agent-lexicon build-evidence examples/customer_limits/docs --root examples/customer_limits --json
agent-lexicon build-evidence examples/customer_limits/docs --root examples/customer_limits --jsonl
```

Python usage:

```python
from agent_lexicon import build_evidence_packs, discover_scout_candidates, ingest_local_paths

ingest_report = ingest_local_paths(["examples/customer_limits/docs"], root="examples/customer_limits")
candidate_report = discover_scout_candidates(ingest_report.documents)
evidence_report = build_evidence_packs(ingest_report.documents, candidate_report.candidates)

for pack in evidence_report.packs:
    print(pack.surface, pack.positive_count, pack.negative_count)
```

Each pack includes the candidate surface, score, positive snippets, negative
snippets, line ranges, reasons, and source metadata. This is the local evidence
foundation for proposal review and future snapshot publishing.


## Prompt safety

Agent Lexicon treats local docs and evidence snippets as untrusted input before
they are shown to a future LLM reviewer. The prompt-safety scanner detects common
prompt-injection patterns such as attempts to override instructions, reveal
system prompts, exfiltrate secrets, or force tool execution. Evidence packs are
annotated with prompt-safety metadata by default, so review workflows can block
high-risk snippets before they are sent to an LLM.

Command line usage:

```bash
agent-lexicon safety scan examples/customer_limits/docs --root examples/customer_limits
agent-lexicon safety scan examples/customer_limits/docs --root examples/customer_limits --json
agent-lexicon safety scan examples/customer_limits/docs --root examples/customer_limits --fail-on-high-risk
agent-lexicon build-evidence examples/customer_limits/docs --root examples/customer_limits
```

Python usage:

```python
from agent_lexicon import (
    format_evidence_pack_for_llm_review,
    ingest_local_paths,
    scan_documents_for_prompt_injection,
)

ingest_report = ingest_local_paths(["examples/customer_limits/docs"], root="examples/customer_limits")
safety_report = scan_documents_for_prompt_injection(ingest_report.documents)

print(safety_report.highest_risk.value, safety_report.action.value)
```

The helper `format_evidence_pack_for_llm_review(...)` renders evidence as
data-only context with explicit untrusted boundaries. This keeps future review
agents from accidentally following instructions embedded inside project docs.


## Local policy modes

Agent Lexicon includes a small local policy layer for review workflows that need
more structure than a single-user scratchpad but do not require an enterprise
RBAC service. The policy is intentionally declarative: the CLI caller declares
an `actor` and optional `role`, then Agent Lexicon checks the requested local
action against the active mode.

Supported modes:

- `solo` — local-first mode where all roles can perform workspace actions.
- `team` — review and export are allowed for reviewers, while sync and snapshot publishing require maintainers or owners.
- `locked` — read-only for everyone except owners.

Create and inspect a local policy file:

```bash
agent-lexicon policy init --root examples/customer_limits --mode team --actor maxim --role owner
agent-lexicon policy status --root examples/customer_limits --actor maxim
agent-lexicon policy check --root examples/customer_limits --action publish_snapshot --actor maxim
```

The same policy checks are used by sensitive local commands:

```bash
agent-lexicon workspace sync examples/customer_limits/docs --root examples/customer_limits --actor maxim
agent-lexicon workspace publish-snapshot --root examples/customer_limits --actor maxim
agent-lexicon review --root examples/customer_limits --actor reviewer-1 --role reviewer
```

Python usage:

```python
from agent_lexicon import PolicyAction, check_local_policy, load_local_policy

policy = load_local_policy("examples/customer_limits")
decision = check_local_policy(
    policy,
    PolicyAction.PUBLISH_SNAPSHOT,
    actor="reviewer-1",
    role="reviewer",
)

print(decision.allowed, decision.reason)
```

This is RBAC-lite, not authentication. It is designed for local workflows, CI,
and future MCP/LLM review boundaries where the agent should know which actions
are allowed before changing review state or publishing snapshots.

## SQLite workspace state

Agent Lexicon can keep local ingest, scout candidates, and evidence packs in a
SQLite workspace under `.agent-lexicon/agent_lexicon.db`. The workspace is a
local cache for review and snapshot workflows. It is safe to delete and rebuild
from project files; team source of truth remains lexicon files, review exports,
and published snapshots.

Command line usage:

```bash
agent-lexicon workspace init --root examples/customer_limits
agent-lexicon workspace sync examples/customer_limits/docs --root examples/customer_limits --max-candidates 5
agent-lexicon workspace status --root examples/customer_limits
agent-lexicon workspace status --root examples/customer_limits --json
agent-lexicon workspace export-review-events --root examples/customer_limits
agent-lexicon workspace export-review-events --root examples/customer_limits --output review-events.jsonl
```

Python usage:

```python
from agent_lexicon import init_workspace, ingest_local_paths

state = init_workspace("examples/customer_limits")
ingest_report = ingest_local_paths(["examples/customer_limits/docs"], root="examples/customer_limits")
state.store_ingest_report(ingest_report)

summary = state.summary()
print(summary.document_count, summary.db_path)
```

The workspace stores documents, candidate payloads, evidence pack payloads,
local review decisions, and append-only review events. The database is designed
for local review workflows without requiring a service backend.

## Local web proposal inbox

The local proposal inbox turns workspace candidates and evidence packs into a
small browser-based review queue. It runs only on localhost by default and does
not require a frontend build, a database server, or a hosted service.

Start by syncing a workspace, then open the inbox:

```bash
agent-lexicon workspace sync examples/customer_limits/docs --root examples/customer_limits --max-candidates 5
agent-lexicon review --root examples/customer_limits
```

The default URL is:

```text
http://127.0.0.1:8765
```

The inbox shows each candidate surface, score, jargon signal, background
penalty, positive evidence, and negative evidence. Reviewer decisions are saved
back to the local SQLite workspace as `accepted`, `rejected`, `ambiguous`, or
`needs_split`. The interface is intentionally minimal: one candidate list, one
evidence card, and one decision area.

For terminal-only environments, keep the server from opening a browser:

```bash
agent-lexicon review --root examples/customer_limits --no-browser
```

## Review events

Every local review decision is also stored as an append-only event. This keeps
the current decision state easy to query while preserving the review trail for
later proposal exports, snapshot publishing, and review dataset analysis. Events
include the decision, reviewer note, timestamp, candidate snapshot, and evidence
snapshot.

Command line usage:

```bash
agent-lexicon workspace export-review-events --root examples/customer_limits
agent-lexicon workspace export-review-events --root examples/customer_limits --decision accepted
agent-lexicon workspace export-review-events --root examples/customer_limits --output review-events.jsonl
```

Python usage:

```python
from agent_lexicon import export_review_events_jsonl, init_workspace

state = init_workspace("examples/customer_limits")
jsonl = export_review_events_jsonl(state)
```

The local web inbox also exposes the same export at `/review-events.jsonl` while
the server is running.

## Canonical migrations

Deprecated terms can declare a replacement canonical term through metadata. This
lets teams keep the old term visible for migration while directing agents and
reviewers toward the active canonical term. Agent Lexicon can also suggest a
conservative migration candidate from surface similarity when no explicit
replacement is declared.

Lexicon example:

```yaml
terms:
  - id: billing.credit_limit
    canonical: credit limit
  - id: billing.customer_cap
    canonical: customer cap
    deprecated: true
    metadata:
      replacement_term_id: billing.credit_limit
```

Command line usage:

```bash
agent-lexicon discover-migrations examples/customer_limits/lexicon.yaml
agent-lexicon discover-migrations examples/customer_limits/lexicon.yaml --json
agent-lexicon discover-migrations examples/customer_limits/lexicon.yaml --jsonl
```

Python usage:

```python
from agent_lexicon import discover_canonical_migration_candidates, load_lexicon

lexicon = load_lexicon("examples/customer_limits/lexicon.yaml")
report = discover_canonical_migration_candidates(lexicon)

for candidate in report.candidates:
    print(candidate.deprecated_term_id, "->", candidate.replacement_term_id)
```

Each migration candidate includes the deprecated term, replacement term,
confidence, risk, rationale, surfaces to preserve as aliases, and a
`canonical_migration` proposal representation. Deprecated surfaces are ignored
by collision validation so a replacement term can safely carry old wording as an
alias after review.

## Behavior metrics

Agent Lexicon can run deterministic behavior checks against a local `queries.jsonl` dataset.
The report measures terminology resolution, ambiguity detection, canonicalization,
and unsafe tool-call prevention.

```bash
agent-lexicon check examples/customer_limits/lexicon.yaml examples/customer_limits/queries.jsonl
```

Example output:

```text
Behavior check: 38/38 checks passed across 5 queries
Overall accuracy: 100.0%
Ambiguity detection: 100.0%
Canonicalization: 100.0%
Wrong tool prevention: 100.0%
Tool status: 100.0%
Tool allowed: 100.0%
```

For automation and dashboards, the same report can be emitted as JSON:

```bash
agent-lexicon check examples/customer_limits/lexicon.yaml examples/customer_limits/queries.jsonl --json
```

## Published local snapshots

Accepted local review decisions can be promoted into a lexicon-compatible JSON
snapshot. The snapshot can be validated and loaded by the same runtime APIs used
for normal lexicon documents. This keeps the local review workflow simple: scout
finds candidates, reviewers accept the terms they trust, and `publish-snapshot`
writes a deterministic artifact for agents and CI.

Command line usage after accepting candidates in the local review inbox:

```bash
agent-lexicon workspace publish-snapshot --root examples/customer_limits
agent-lexicon workspace publish-snapshot --root examples/customer_limits --output examples/customer_limits/snapshot.json
agent-lexicon workspace publish-snapshot --root examples/customer_limits --lexicon examples/customer_limits/lexicon.yaml --json
agent-lexicon validate examples/customer_limits/snapshot.json
```

Python usage:

```python
from agent_lexicon import open_workspace, publish_local_snapshot

state = open_workspace("examples/customer_limits")
snapshot = publish_local_snapshot(
    state,
    output_path="examples/customer_limits/snapshot.json",
)

print(snapshot.snapshot_id, snapshot.generated_term_count)
```

Only `accepted` review decisions are promoted. Rejected, ambiguous, and
needs-split candidates remain in the workspace review history and can still be
exported through the review events JSONL workflow.

## Dictionary-as-code layout

Agent Lexicon separates local workspace state from git-tracked dictionary files.
The `.agent-lexicon/` directory stores SQLite cache, local review decisions, and
workspace metadata. The `lexicon/` directory is the reviewable source of truth
that can be committed, reviewed in pull requests, validated in CI, and used by
runtime agents.

Create the standard layout:

```bash
agent-lexicon dictionary init --root .
```

The command creates:

```text
lexicon/
  README.md
  lexicon.yaml
  queries.jsonl
  proposals/
  snapshots/
  review-events/
```

Validate the layout before opening a pull request:

```bash
agent-lexicon dictionary validate --root .
agent-lexicon check lexicon/lexicon.yaml lexicon/queries.jsonl
```

Python usage:

```python
from agent_lexicon import init_dictionary_layout, validate_dictionary_layout

summary = init_dictionary_layout(".")
assert summary.valid

validated = validate_dictionary_layout(".")
print(validated.layout.lexicon_path)
```

The layout command preserves existing files by default. Use `--force` only when
you intentionally want to overwrite the generated starter files.

## Semantic diff

Agent Lexicon can compare two validated lexicon files by terminology semantics
instead of raw line changes. This keeps pull request review focused on canonical
terms, aliases, scopes, tool mappings, evidence, proposals, and metadata.

```bash
agent-lexicon dictionary diff lexicon/lexicon.yaml lexicon-next.yaml
agent-lexicon dictionary diff lexicon/lexicon.yaml lexicon-next.yaml --json
agent-lexicon dictionary diff lexicon/lexicon.yaml lexicon-next.yaml --fail-on-change
```

Python usage:

```python
from agent_lexicon import diff_lexicon_files

report = diff_lexicon_files("lexicon/lexicon.yaml", "lexicon-next.yaml")
for change in report.changes:
    print(change.to_text())
```

Use `--fail-on-change` in automation when a workflow needs to detect whether a
lexicon update contains any semantic changes.

## Semantic merge

Agent Lexicon can perform a three-way merge for lexicon files using terminology
objects instead of raw text. Non-overlapping changes are merged automatically;
competing edits to the same semantic field become conflicts that can be handled
in review before publishing a dictionary update.

```bash
agent-lexicon dictionary merge lexicon-base.yaml lexicon-ours.yaml lexicon-theirs.yaml --output lexicon-merged.json
agent-lexicon dictionary merge lexicon-base.yaml lexicon-ours.yaml lexicon-theirs.yaml --check
agent-lexicon dictionary merge lexicon-base.yaml lexicon-ours.yaml lexicon-theirs.yaml --json
```

Python usage:

```python
from agent_lexicon import merge_lexicon_files, write_merged_lexicon_json

report = merge_lexicon_files(
    "lexicon-base.yaml",
    "lexicon-ours.yaml",
    "lexicon-theirs.yaml",
)

if report.has_conflicts:
    for conflict in report.conflicts:
        print(conflict.to_text())
else:
    write_merged_lexicon_json(report, "lexicon-merged.json")
```

Use semantic merge when multiple branches update terminology at the same time.
It can combine independent additions such as new aliases, tools, metadata, or
evidence while blocking ambiguous edits such as two different canonical names for
the same term.

## CI and pull request validation

Agent Lexicon includes a single dictionary PR check command for local CI and
GitHub Actions. It validates the git-tracked dictionary layout, runs the behavior
dataset, optionally prints a semantic diff against a base lexicon, and can check
three-way semantic merge inputs for conflicts.

```bash
agent-lexicon dictionary pr-check --root .
agent-lexicon dictionary pr-check --root . --base-lexicon /tmp/base-lexicon.yaml
agent-lexicon dictionary pr-check --root . --base-lexicon /tmp/base-lexicon.yaml --json
```

For stricter automation, fail when any semantic change is detected:

```bash
agent-lexicon dictionary pr-check --root . --base-lexicon /tmp/base-lexicon.yaml --fail-on-semantic-change
```

For merge validation, provide all three merge inputs together:

```bash
agent-lexicon dictionary pr-check --root . --merge-base base.yaml --merge-ours ours.yaml --merge-theirs theirs.yaml
```

The repository also includes a `Dictionary` GitHub Actions workflow that runs the
PR check on `main` pushes and pull requests. On pull requests, it attempts to
load the base branch `lexicon/lexicon.yaml` and includes a semantic diff in the
check output.

## Development

Install the development environment with Poetry:

```bash
poetry install --with dev
```

Run the test suite:

```bash
poetry run pytest -q
```

The repository also includes Make targets for the same workflow:

```bash
make install
make test
make check
```

## Relationship to SkeinRank

Agent Lexicon is intended to be the lightweight runtime SDK that agents can call
locally. SkeinRank remains the enterprise control plane for terminology drift,
proposal review, governed snapshots, and search/RAG integration.

## License

Apache License 2.0.

## Eval query datasets

Agent Lexicon uses JSONL query datasets to describe expected runtime behavior.
Each row contains one user query, optional scopes, expected terminology
resolution, and optional tool-call safety expectations. Metrics are computed by
the evaluation runner, while this layer keeps the dataset format validated and
portable.

Example row:

```json
{"id":"ambiguous.limit","text":"increase the limit","expected_status":"ambiguous","expected_action":"ask_clarification","expected_term_ids":["billing.credit_limit","api.rate_limit"],"tool_calls":[{"tool_name":"api.update_rate_limit","expected_status":"needs_clarification","expected_action":"ask_clarification","expected_allowed":false}]}
```

Validate a dataset from the command line:

```bash
agent-lexicon validate-queries examples/customer_limits/queries.jsonl
agent-lexicon check examples/customer_limits/lexicon.yaml examples/customer_limits/queries.jsonl
agent-lexicon ingest README.md src examples/customer_limits/docs --root .
agent-lexicon discover-candidates examples/customer_limits/docs --root examples/customer_limits
agent-lexicon build-evidence examples/customer_limits/docs --root examples/customer_limits
agent-lexicon workspace sync examples/customer_limits/docs --root examples/customer_limits --max-candidates 5
```

Load the same dataset from Python:

```python
from agent_lexicon import load_eval_queries

queries = load_eval_queries("examples/customer_limits/queries.jsonl")
assert queries[0].expected_status.value == "ambiguous"
```

The loader validates duplicate ids, JSONL structure, expected resolver statuses,
expected resolver actions, tool guard statuses, tool guard actions, and primary
term references before returning typed query objects.




## Review Agent

Agent Lexicon can produce a local pre-review recommendation for one workspace
candidate. The review agent prepares a safe data-only prompt for optional LLM
review, validates structured LLM responses, and falls back to a deterministic
local assessment when no model response is provided.

```bash
agent-lexicon workspace sync examples/customer_limits/docs --root examples/customer_limits --max-candidates 5
agent-lexicon review-agent prompt --root examples/customer_limits --surface billing.update_credit_limit
agent-lexicon review-agent assess --root examples/customer_limits --surface billing.update_credit_limit
agent-lexicon review-agent consensus --root examples/customer_limits --surface billing.update_credit_limit
agent-lexicon review-agent consensus --root examples/customer_limits --surface billing.update_credit_limit --json
agent-lexicon review-agent assess --root examples/customer_limits --surface billing.update_credit_limit --json
```

The prompt command marks project evidence as untrusted data and uses the prompt
safety layer before content is sent to an external LLM. The assess command
returns one of `accept`, `reject`, `needs_split`, or `needs_more_evidence` and
includes the matching workspace review status for downstream tools. The
consensus command aggregates multiple structured review samples when supplied and
abstains when agreement or confidence is too low.

Python usage:

```python
from agent_lexicon import open_workspace, run_review_agent, run_review_agent_consensus

state = open_workspace("examples/customer_limits")
item = state.get_review_item("billing.update_credit_limit")
assert item is not None
decision = run_review_agent(item)
consensus = run_review_agent_consensus(item)
print(decision.recommendation.value, consensus.status.value)
```

Structured model responses can be passed through `run_review_agent(...,
llm_response=...)` or the CLI `--llm-response` option. High-risk prompt-safety
findings block LLM review and return a safer `needs_more_evidence` decision.

Consensus mode accepts one or more `--llm-response` files. If samples disagree or
the top decision is below the confidence threshold, Agent Lexicon returns an
abstention instead of a silent low-confidence proposal.

```bash
agent-lexicon review-agent consensus --root examples/customer_limits --surface billing.update_credit_limit --llm-response sample-a.json --llm-response sample-b.json
```


## Review dataset quality loop

Local review decisions can be exported as quality-labeled JSONL examples for
future evals, regression tests, and optional model improvement workflows. The
dataset export keeps human decisions separate from Review Agent suggestions so
the examples remain auditable.

```bash
agent-lexicon review-agent dataset --root examples/customer_limits
agent-lexicon review-agent dataset --root examples/customer_limits --json
agent-lexicon review-agent dataset --root examples/customer_limits --quality usable --output review-dataset.jsonl
agent-lexicon review-agent dataset --root examples/customer_limits --include-review-agent --json
```

Each exported row includes the candidate snapshot, evidence snapshot, human
decision, reviewer note, quality label, quality flags, and optional Review Agent
recommendation. Quality labels are `usable`, `incomplete`, `conflicting`,
`unsafe`, and `low_signal`.

Python usage:

```python
from agent_lexicon import build_review_dataset, open_workspace

state = open_workspace("examples/customer_limits")
report = build_review_dataset(state, include_review_agent=True)
print(report.usable_count)
```

This layer is intentionally local and dependency-free. It prepares portable
examples that can later be evaluated, filtered, or imported into a larger
governance system without sending private evidence to external services.

## MCP server

Agent Lexicon can run a local Model Context Protocol stdio server so desktop
agents and coding agents can use the same terminology checks as the CLI. The
server is dependency-free, local-first, and reads the git-tracked lexicon plus
optional `.agent-lexicon/` workspace state.

```bash
agent-lexicon mcp tools
agent-lexicon mcp serve --root . --lexicon lexicon/lexicon.yaml
```

The local MCP server exposes these tools:

- `resolve_term` — resolve text and return `resolved`, `ambiguous`, or `unknown`.
- `check_language` — check whether text is safe for an agent to interpret directly.
- `guard_tool_call` — block unsafe tool calls when terminology is ambiguous or mismatched.
- `find_evidence` — return lexicon or workspace evidence for a term or surface.
- `submit_proposal` — save a local review decision under workspace policy.
- `get_snapshot` — return published local snapshot metadata.

For local clients, point the MCP command at your repository root and lexicon file.
Sensitive write actions use the same RBAC-lite policy layer as the workspace and
review commands.
