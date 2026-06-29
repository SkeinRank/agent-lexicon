# Agent Lexicon Dictionary

This directory is the git-tracked terminology source of truth for Agent Lexicon.

## Files

- `lexicon.yaml` — canonical terms, aliases, scopes, tools, and evidence.
- `queries.jsonl` — behavior checks for ambiguity, canonicalization, and tool safety.
- `proposals/` — reviewable terminology proposal files.
- `snapshots/` — published local snapshots when the team wants to keep them in git.
- `review-events/` — curated review-event exports when they are intentionally committed.

Local SQLite state remains outside git under `.agent-lexicon/`.

## Commands

```bash
agent-lexicon dictionary validate --root . --layout-dir lexicon
agent-lexicon check lexicon/lexicon.yaml lexicon/queries.jsonl
```
