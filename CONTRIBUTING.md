# Contributing to Agent Lexicon

Thanks for your interest in improving Agent Lexicon. This project is an early,
actively developed terminology layer for AI agents, and contributions of all
sizes are welcome — bug reports, docs, tests, and features.

By participating you agree to abide by our [Code of Conduct](CODE_OF_CONDUCT.md).

## Development setup

Agent Lexicon requires **Python 3.10+** and uses [Poetry](https://python-poetry.org)
for dependency management.

```bash
git clone https://github.com/SkeinRank/agent-lexicon
cd agent-lexicon
poetry install --with dev
```

Run the test suite:

```bash
poetry run pytest
```

The core has zero runtime dependencies. Optional extras (`oov`, `semantic`)
are installed on demand and must never be imported on the deterministic
resolve or guard path.

## The one rule that matters most

Agent Lexicon's value is that its **decisions are deterministic and
auditable**. The resolve, guard, and heuristic drift-classification paths must
never call a model, compute an embedding, or introduce randomness.

- Anything that **decides** stays deterministic.
- Anything that only **suggests** to a human (semantic reranking, near-miss
  hints) may be smart and non-deterministic, but must be clearly marked as a
  suggestion and must never commit a decision on its own.

If a change blurs that boundary, it will be asked to change, regardless of how
useful the feature is.

## Making a change

1. Open an issue first for anything non-trivial, so we can agree on the
   approach before you invest time.
2. Fork the repo and create a branch from `main`.
3. Keep the change focused. One logical change per pull request.
4. Add or update tests. New behavior without a test will not be merged.
5. Update the docs in `docs/` and the `README.md` if you change public
   behavior or the public API surface.

## Before you open a pull request

Please make sure the following pass locally:

```bash
poetry run pytest                                   # tests
poetry run agent-lexicon validate lexicon/lexicon.yaml --lint --strict-lint
```

- Keep public API changes reflected in `docs/api.md`.
- Keep MCP tool changes reflected in `docs/mcp.md`.
- Follow the existing code style: type hints everywhere, frozen dataclasses for
  models, explicit errors over silent fallback.
- Write clear commit messages that explain *why*, not just *what*.

## Reporting bugs

Open an issue with:

- What you did (ideally a minimal reproduction).
- What you expected to happen.
- What actually happened, including the full error output.
- Your Python version and Agent Lexicon version (`agent-lexicon --version`).

## Security issues

Please do **not** open a public issue for a security vulnerability. See
[SECURITY.md](SECURITY.md) for how to report privately.

## License

By contributing, you agree that your contributions will be licensed under the
[Apache License 2.0](LICENSE), the same license that covers this project.
