.PHONY: install dev lint typecheck test test-all check clean

install:
	pip install -e .

dev:
	pip install -e ".[dev]"

lint:
	ruff check .

typecheck:
	mypy .

test:
	pytest tests/ -m "not integration" -v

test-all:
	pytest tests/ -v

check: lint typecheck test

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .mypy_cache -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	rm -rf build/ dist/ *.egg-info
