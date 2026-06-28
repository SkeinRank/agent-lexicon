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
agent-lexicon resolve examples/customer_limits/lexicon.yaml "increase the limit" --scope billing
```

This gives agents a local way to stop before unsafe assumptions: if the same
surface can mean multiple canonical terms, the recommended action is
`ask_clarification`.

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
