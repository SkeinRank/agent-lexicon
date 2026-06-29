.PHONY: install test test-unit dictionary-check check ci clean

install:
	poetry install --with dev

test:
	poetry run pytest -q

test-unit:
	poetry run pytest -q tests

dictionary-check:
	poetry run python -m agent_lexicon dictionary pr-check --root .

check: test dictionary-check

ci: check

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache dist build *.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
