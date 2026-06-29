# Agent Lexicon

Shared terminology memory for AI agents.

Agent Lexicon is a lightweight Python package for giving agents a small,
reviewable terminology layer before RAG, tool calls, and workflow automation.

## Install

```bash
pip install agent-lexicon
```

## Quick check

```bash
agent-lexicon --version
python -m agent_lexicon --version
agent-lexicon validate examples/customer_limits/lexicon.yaml
agent-lexicon match examples/customer_limits/lexicon.yaml "The customer cap and rate limit changed." --longest-only
agent-lexicon resolve examples/customer_limits/lexicon.yaml "increase the limit"
agent-lexicon guard examples/customer_limits/lexicon.yaml "increase the limit" --tool api.update_rate_limit
agent-lexicon validate-queries examples/customer_limits/queries.jsonl
agent-lexicon check examples/customer_limits/lexicon.yaml examples/customer_limits/queries.jsonl
agent-lexicon ingest README.md src examples/customer_limits/docs --root .
agent-lexicon discover-candidates examples/customer_limits/docs --root examples/customer_limits
agent-lexicon build-evidence examples/customer_limits/docs --root examples/customer_limits
agent-lexicon workspace init --root examples/customer_limits
agent-lexicon workspace sync examples/customer_limits/docs --root examples/customer_limits --max-candidates 5
agent-lexicon workspace status --root examples/customer_limits
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

The workspace stores documents, candidate payloads, and evidence pack payloads
with deterministic primary keys. Later review, event, and snapshot workflows can
use the same local database without requiring a service backend.

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

