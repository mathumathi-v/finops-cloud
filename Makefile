.PHONY: install dev lint typecheck test test-all check clean docker-build docker-run

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

docker-build:
	docker build -t finops-agent:latest .

docker-run:
	docker run --rm -v ~/.finops-agent:/home/finops/.finops-agent \
		-v ~/.aws:/home/finops/.aws:ro \
		-v ~/.oci:/home/finops/.oci:ro \
		finops-agent:latest $(CMD)
