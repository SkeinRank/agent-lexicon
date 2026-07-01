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

This creates a local dictionary-as-code layout under `lexicon/`, a local SQLite-backed workspace under `.agent-lexicon/`, and `.agent-lexicon/config.yaml` with default scan paths for `README.md`, `docs`, and `src`. The workspace is opened through a storage boundary, so local workflows stay lightweight while future shared storage can plug into the same review/provenance APIs.

## 3. Discover candidate terms

Run `scan` with the repository defaults from `.agent-lexicon/config.yaml`:

```bash
agent-lexicon scan
```

You can still override paths for a one-off run:

```bash
agent-lexicon scan README.md docs src
```

Scan reads the configured files, applies include/exclude rules, runs prompt-safety checks, discovers candidate terms, builds line-numbered evidence for each, scores their quality, and saves everything to the local workspace. Add `--quality-report` to see how candidates were prioritized.

Repository scan behavior lives in `.agent-lexicon/config.yaml`:

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

CLI flags override config for one run.

## 4. Review

```bash
agent-lexicon review
```

This opens a local web inbox in your browser. Each candidate shows its evidence; you accept, reject, or mark it ambiguous. Nothing leaves your machine.

Each saved review decision is also appended to the workspace decision provenance log. Export it when you want an auditable JSONL trail of who decided what and why:

```bash
agent-lexicon workspace export-decision-log --action review_decision_saved
```

## 5. Publish a snapshot

```bash
agent-lexicon publish
```

Accepted decisions become a versioned lexicon snapshot. The publish summary includes immutable snapshot metadata, including a `lexicon_snapshot_ref` (`sha256:<digest>`), so later runtime and review decisions can point back to the exact vocabulary content they used.

## 6. Use it at runtime

Resolve terminology in any text:

```bash
agent-lexicon resolve lexicon/lexicon.yaml "please raise the limit" --scope billing
```

The output includes the immutable lexicon snapshot reference used for the decision:

```text
Lexicon snapshot: sha256:<digest>
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

Before a branch lands, check what terminology it introduced. The report includes the lexicon snapshot reference used for the review:

```bash
agent-lexicon check-merge --base main --head feature-branch --include 'src/**'
```

Add `--fail-on-review` in CI to block a merge when unreviewed terminology appears.
