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
```

## Core schema

The package includes dependency-free core models for terminology workflows:

- `Scope` — a project, team, domain, or workflow boundary where a term has a specific meaning.
- `Term` — a canonical domain term with aliases, scopes, tags, evidence, and metadata.
- `Alias` — a surface form that points to a canonical term.
- `EvidenceSpan` — a source-backed snippet with file path, line range, and evidence kind.
- `ProposalCandidate` — a reviewable terminology change suggested by local analysis or an agent.

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
