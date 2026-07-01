# Quickstart

This walks you from an empty project to a published terminology snapshot, then shows how agents use it at runtime.

## 1. Install

```bash
pip install agent-lexicon
```

Requires Python 3.10+. The core has no runtime dependencies.

## 2. Initialize a project

```bash
agent-lexicon init
```

This creates a local dictionary-as-code layout under `lexicon/`, with a starter `lexicon.yaml` you will grow over time.

## 3. Discover candidate terms

Point `scan` at the files where your terminology lives — docs, READMEs, source:

```bash
agent-lexicon scan README.md docs src
```

Scan reads the files, runs prompt-safety checks on them, discovers candidate terms, builds line-numbered evidence for each, scores their quality, and saves everything to the local workspace. Add `--quality-report` to see how candidates were prioritized.

## 4. Review

```bash
agent-lexicon review
```

This opens a local web inbox in your browser. Each candidate shows its evidence; you accept, reject, or mark it ambiguous. Nothing leaves your machine.

## 5. Publish a snapshot

```bash
agent-lexicon publish
```

Accepted decisions become a versioned lexicon snapshot. This snapshot is the canonical vocabulary your agents read from.

## 6. Use it at runtime

Resolve terminology in any text:

```bash
agent-lexicon resolve lexicon/lexicon.yaml "please raise the limit" --scope billing
```

Gate a tool call:

```bash
agent-lexicon guard lexicon/lexicon.yaml "raise the credit limit" --tool billing.update_credit_limit
```

Or expose everything to an MCP-compatible agent:

```bash
agent-lexicon mcp serve --root . --lexicon lexicon/lexicon.yaml
```

## 7. Catch drift at merge

Before a branch lands, check what terminology it introduced:

```bash
agent-lexicon check-merge --base main --head feature-branch --include 'src/**'
```

Add `--fail-on-review` in CI to block a merge when unreviewed terminology appears.
