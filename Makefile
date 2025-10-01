.PHONY: help install install-dev install-redis install-all clean build format lint type-check test test-cov publish-test publish docker-build docker-run sync

# UV commands (modern, fast Python package manager)
UV := uv
UV_RUN := uv run
PYTHON := python3

# Package name
PACKAGE_NAME := resilience_h8

help: ## Show this help message
	@echo "================================================================"
	@echo "          Resilience H8 - Development Makefile"
	@echo "================================================================"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-20s %s\n", $$1, $$2}'
	@echo ""

sync: ## Sync dependencies from uv.lock (fastest, recommended)
	@echo "Syncing dependencies with uv..."
	$(UV) sync --all-extras
	@echo "Dependencies synced from lock file"

install: ## Install package dependencies with uv
	@echo "Installing package dependencies with uv..."
	$(UV) pip install -e .
	@echo "Package installed successfully"

install-dev: ## Install package with development dependencies
	@echo "Installing development dependencies with uv..."
	$(UV) pip install -e ".[dev]"
	@echo "Development dependencies installed"

install-redis: ## Install package with Redis support
	@echo "Installing Redis dependencies with uv..."
	$(UV) pip install -e ".[redis]"
	@echo "Redis dependencies installed"

install-all: ## Install package with all optional dependencies
	@echo "Installing all dependencies with uv..."
	$(UV) pip install -e ".[dev,redis,all]"
	@echo "All dependencies installed"

clean: ## Clean build artifacts and cache files
	@echo "Cleaning build artifacts..."
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info
	rm -rf src/*.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete
	find . -type f -name "*.pyd" -delete
	find . -type d -name "*.egg" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".coverage" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name ".coverage" -delete
	find . -type d -name "htmlcov" -exec rm -rf {} + 2>/dev/null || true
	@echo "Cleanup complete"

build: clean ## Build distribution packages
	@echo "Building distribution packages with uv..."
	$(UV) build
	@echo "Build complete"
	@ls -lh dist/

format: ## Format code with ruff
	@echo "Formatting code with ruff..."
	$(UV_RUN) ruff format src/ examples/
	@echo "Fixing imports and code with ruff..."
	$(UV_RUN) ruff check --fix src/ examples/
	@echo "Code formatting complete"

format-check: ## Check code formatting without modifying files
	@echo "Checking code formatting..."
	$(UV_RUN) ruff format --check src/ examples/
	$(UV_RUN) ruff check src/ examples/
	@echo "Format check complete"

lint: ## Run linting with ruff
	@echo "Running ruff linter..."
	$(UV_RUN) ruff check src/ examples/
	@echo "Linting complete"

lint-fix: ## Run linting with ruff and auto-fix issues
	@echo "Running ruff linter with auto-fix..."
	$(UV_RUN) ruff check --fix src/ examples/
	@echo "Linting with auto-fix complete"

type-check: ## Run type checking with mypy
	@echo "Running mypy type checker..."
	$(UV_RUN) mypy src/$(PACKAGE_NAME) --config-file=mypy.ini --exclude='src/resilience_h8/storage/redis_backend.py'
	@echo "Type checking complete"

test: ## Run tests with pytest
	@echo "Running tests..."
	$(UV_RUN) pytest tests/ -v --tb=short
	@echo "Tests complete"
test-single: ## Run tests with pytest for a single file (usage: make test-single TEST_NAME=test_integration.py::test_rate_limiter_pattern)
	@echo "Running tests for $(TEST_NAME)..."
	$(UV_RUN) pytest tests/$(TEST_NAME) -v --tb=short
	@echo "Tests complete"

test-redis: redis-check ## Run all tests including Redis integration tests
	@echo "Running tests with Redis integration..."
	$(UV_RUN) pytest tests/ -v --tb=short --redis
	@echo "All tests (including Redis) complete"

test-cov: ## Run tests with coverage report
	@echo "Running tests with coverage..."
	$(UV_RUN) pytest tests/ -v --cov=src/$(PACKAGE_NAME) --cov-report=html --cov-report=term-missing
	@echo "Coverage report generated in htmlcov/"

test-watch: ## Run tests in watch mode
	@echo "Running tests in watch mode..."
	$(UV_RUN) pytest-watch tests/ -v

check-all: format-check lint type-check ## Run all checks (format, lint, type)
	@echo "All checks passed!"

run: ## Run examples with uv (usage: make run FILE=examples/redis_distributed_example.py)
	@echo "Running $(FILE)..."
	$(UV_RUN) python $(FILE)

publish-test: build ## Publish to TestPyPI
	@echo "Publishing to TestPyPI..."
	$(UV) publish --publish-url https://test.pypi.org/legacy/
	@echo "Published to TestPyPI"

publish: build ## Publish to PyPI
	@echo "WARNING: Publishing to PyPI (production)..."
	@read -p "Are you sure? [y/N] " -n 1 -r; \
	echo; \
	if [[ $$REPLY =~ ^[Yy]$$ ]]; then \
		$(UV) publish; \
		echo "Published to PyPI"; \
	else \
		echo "Publish cancelled"; \
	fi

docker-build: ## Build Docker image for testing
	@echo "Building Docker image..."
	docker build -t $(PACKAGE_NAME):latest .
	@echo "Docker image built"

docker-run: ## Run Docker container with Redis
	@echo "Starting Docker containers..."
	docker-compose up -d
	@echo "Containers started"

docker-stop: ## Stop Docker containers
	@echo "Stopping Docker containers..."
	docker-compose down
	@echo "Containers stopped"

redis-check: ## Check if Redis is running on localhost:6379
	@echo "Checking Redis connection..."
	@nc -zv localhost 6379 > /dev/null 2>&1 && echo "Redis is running on localhost:6379" || (echo "ERROR: Redis is not running. Start with: make redis-start" && exit 1)

redis-start: ## Start local Redis for development
	@echo "Starting Redis container..."
	docker run -d --name resilience-redis -p 6379:6379 redis:7-alpine
	@echo "Redis started on localhost:6379"

redis-stop: ## Stop local Redis container
	@echo "Stopping Redis container..."
	docker stop resilience-redis
	docker rm resilience-redis
	@echo "Redis stopped"

redis-cli: ## Connect to Redis CLI
	@echo "Connecting to Redis CLI..."
	docker exec -it resilience-redis redis-cli

upgrade-deps: ## Upgrade all dependencies to latest versions
	@echo "Upgrading dependencies with uv..."
	$(UV) pip install --upgrade -e ".[dev,redis,all]"
	@echo "Dependencies upgraded"

lock: ## Update uv.lock file with latest compatible versions
	@echo "Updating lock file..."
	$(UV) lock --upgrade
	@echo "Lock file updated"

init-dev: sync ## Initialize development environment (fast!)
	@echo "Initializing development environment with uv..."
	@echo "Development environment ready!"
	@echo ""
	@echo "Quick Start:"
	@echo "  make sync        - Sync dependencies from lock file (fastest!)"
	@echo "  make format      - Format code"
	@echo "  make lint        - Run linter"
	@echo "  make type-check  - Run type checker"
	@echo "  make test        - Run tests"
	@echo "  make redis-start - Start Redis for development"

ci: clean sync check-all test-cov ## Run full CI pipeline with uv
	@echo "CI pipeline complete!"

version: ## Show package version
	@echo "Package Version: $(shell grep "^version" pyproject.toml | cut -d'"' -f2)"

bump-patch: ## Bump patch version (0.1.6 -> 0.1.7)
	@echo "Bumping patch version..."
	$(UV_RUN) python scripts/bump_version.py patch
	@echo "Version bumped"

bump-minor: ## Bump minor version (0.1.6 -> 0.2.0)
	@echo "Bumping minor version..."
	$(UV_RUN) python scripts/bump_version.py minor
	@echo "Version bumped"

bump-major: ## Bump major version (0.1.6 -> 1.0.0)
	@echo "Bumping major version..."
	$(UV_RUN) python scripts/bump_version.py major
	@echo "Version bumped"

tag-version: ## Create git tag for current version
	@VERSION=$$(grep "^version" pyproject.toml | cut -d'"' -f2); \
	echo "Creating git tag v$$VERSION..."; \
	git tag -a "v$$VERSION" -m "Release v$$VERSION"; \
	echo "Tag created: v$$VERSION"; \
	echo "Push with: git push origin main --tags"

release-check: check-all test-redis ## Run all checks before release
	@echo "All release checks passed!"
	@echo ""
	@echo "Ready to release! Next steps:"
	@echo "  1. Bump version: make bump-minor (or bump-patch/bump-major)"
	@echo "  2. Commit changes: git commit -am 'chore: bump version to X.Y.Z'"
	@echo "  3. Create tag: make tag-version"
	@echo "  4. Push: git push origin main --tags"
	@echo "  5. Build: make build"
	@echo "  6. Publish: make publish (or make publish-test for TestPyPI)"

.DEFAULT_GOAL := help
