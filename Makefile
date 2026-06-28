.PHONY: install test test-unit check ci clean

install:
	poetry install --with dev

test:
	poetry run pytest -q

test-unit:
	poetry run python -m unittest discover -s tests -v

check: test

ci: check

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache dist build *.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
