# Quickstart

This walks you from an empty project to a published terminology snapshot, then shows how agents use it at runtime.

## 1. Install

As a command-line tool (recommended), install with pipx so it is available everywhere:

```bash
pipx install agent-lexicon
```

Or with pip, to use it as a library inside a project:

```bash
pip install agent-lexicon
```

Requires Python 3.10+. The core has no runtime dependencies.

Every command is also available under the short alias `alex` (for example,
`alex scan` instead of `agent-lexicon scan`).

Optional shell tab-completion for commands and flags:

```bash
pip install "agent-lexicon[completion]"
activate-global-python-argcomplete   # once, to enable completion in your shell
```

Completion is opt-in; without the extra installed, the CLI works exactly the
same, just without tab-completion.

## 2. Initialize a project

```bash
agent-lexicon init
```

This creates a local dictionary-as-code layout under `lexicon/`, a local SQLite-backed workspace under `.agent-lexicon/`, and `.agent-lexicon/config.yaml` with default scan paths for `README.md`, `docs`, `src`, and common source roots. The workspace is opened through a storage boundary, so local workflows stay lightweight while future shared storage can plug into the same review/provenance APIs.

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
    - app
    - packages
    - lib
    - services
  include:
    - "docs/**/*.md"
    - "docs/**/*.txt"
    - "**/*.py"
    - "**/*.ts"
    - "**/*.tsx"
    - "**/*.go"
    - "**/*.rs"
    - "**/*.java"
    - "**/*.kt"
    - "**/*.cs"
    - "**/*.sql"
    - "**/*.yaml"
  exclude:
    - ".venv/**"
    - "node_modules/**"
    - "dist/**"
    - "**/generated/**"
  respect_gitignore: true
  max_file_bytes: 1000000
```

The generated config respects `.gitignore` by default. Set `scan.respect_gitignore: false` or run `agent-lexicon scan --no-gitignore` for a one-off audit that intentionally includes ignored files. CLI flags override config for one run.

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
agent-lexicon publish --update-lexicon
```

Accepted decisions become a versioned lexicon snapshot. The publish summary includes immutable snapshot metadata, including a `lexicon_snapshot_ref` (`sha256:<digest>`), so later runtime and review decisions can point back to the exact vocabulary content they used.

With `--update-lexicon`, the published terms are also written back to your git-tracked `lexicon/lexicon.yaml`, so the dictionary-as-code file stays the source of truth and the runtime commands below can keep pointing at it. Without the flag, `publish` only writes the snapshot JSON under `.agent-lexicon/snapshots/` and your YAML is left untouched - in that case, pass the snapshot path to the runtime commands instead of `lexicon/lexicon.yaml`. Note: `--update-lexicon` rewrites the file, so hand-written YAML comments are not preserved.

Review tip: when the scanner offers both a bare attribute (`map_index`) and receiver-prefixed variants (`TaskInstance.map_index`), accept the bare form - it matches the identifier wherever it appears, while the dotted form only matches that exact attribute access.

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

For scripting, add `--json` to `resolve`, `guard`, or `match` to get the full
decision as a machine-readable JSON document on stdout. Exit codes are stable:
`guard` returns `0` when a call is allowed, `2` when it is blocked or needs
clarification. Diagnostics go to stderr, so they never pollute a piped result.

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

## 8. Add the GitHub Actions workflow

The project ships a pull-request workflow at `.github/workflows/agent-lexicon.yml`. It validates `lexicon/lexicon.yaml` and reviews merge-time terminology drift with the same `check-merge` command you run locally.

The workflow is non-blocking by default so a team can introduce terminology review without breaking every PR. Run it manually with `base_ref`, `head_ref`, and `fail_on_review=true`, or add `--fail-on-review` to make unreviewed drift a required merge gate.

