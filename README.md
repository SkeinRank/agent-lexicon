# Agent Lexicon

Shared terminology memory for AI agents.

Agent Lexicon is a lightweight Python package for giving agents a small,
reviewable terminology layer before RAG, tool calls, and workflow automation.

This repository currently contains the initial package skeleton for the first
`0.0.1` release. The full runtime architecture will be added incrementally.

## Install

```bash
pip install agent-lexicon
```

## Quick check

```bash
agent-lexicon --version
python -m agent_lexicon --version
```

## Status

`0.0.1` is an initialization release used to reserve the package name and start
the public repository.

## Relationship to SkeinRank

Agent Lexicon is intended to be the lightweight runtime SDK that agents can call
locally. SkeinRank remains the enterprise control plane for terminology drift,
proposal review, governed snapshots, and search/RAG integration.

## License

Apache License 2.0.
