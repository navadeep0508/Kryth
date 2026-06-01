.PHONY: help install dev test lint format type-check clean docker-build docker-run docs

# Default target
help:
	@echo "KRYTH - Autonomous AI Coding Agent"
	@echo "Available make targets:"
	@echo ""
	@echo "  install       Install dependencies (editable mode)"
	@echo "  dev           Set up development environment"
	@echo "  test          Run test suite with coverage"
	@echo "  lint          Run linters (flake8, black check, isort check)"
	@echo "  format        Format code (black, isort)"
	@echo "  type-check    Run type checking (mypy)"
	@echo "  all-checks    Run all quality checks (format, lint, type-check, test)"
	@echo "  clean         Clean build artifacts and caches"
	@echo "  docker-build  Build Docker image"
	@echo "  docker-run    Run Docker container"
	@echo "  docs          Build documentation"
	@echo "  pre-commit    Install pre-commit hooks"
	@echo "  version       Show KRYTH version"
	@echo ""

# Install dependencies
install:
	@echo "Installing KRYTH in editable mode..."
	uv pip install -e ".[bridge]"
	@echo "Installation complete!"

# Development setup
dev: install pre-commit
	@echo "Development environment ready!"
	@echo "Don't forget to:"
	@echo "  1. Copy .env.example to .env"
	@echo "  2. Add your OPENAI_API_KEY to .env"
	@echo "  3. Run: playwright install chromium"

# Run tests
test:
	@echo "Running tests..."
	pytest --cov=agent --cov-report=term-missing --cov-report=html
	@echo "Tests complete! Coverage report in htmlcov/index.html"

# Run linters
lint:
	@echo "Running linters..."
	flake8 src/ tests/
	black --check src/ tests/
	isort --check-only src/ tests/
	@echo "Linting passed!"

# Format code
format:
	@echo "Formatting code..."
	black src/ tests/
	isort src/ tests/
	@echo "Code formatted!"

# Type checking
type-check:
	@echo "Running type checks..."
	mypy src/
	@echo "Type checking complete!"

# Run all quality checks
all-checks: format lint type-check test
	@echo "All checks passed!"

# Clean build artifacts
clean:
	@echo "Cleaning..."
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "htmlcov" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "dist" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "build" -exec rm -rf {} + 2>/dev/null || true
	rm -f .coverage
	@echo "Clean complete!"

# Docker
docker-build:
	@echo "Building Docker image..."
	docker build -t kryth:latest .
	@echo "Docker image built: kryth:latest"

docker-run:
	@echo "Running KRYTH in Docker..."
	docker run -it --rm -v $(PWD):/workspace -v ~/.kryth:/root/.kryth kryth:latest

# Documentation
docs:
	@echo "Building documentation..."
	@echo "No Sphinx docs configured yet. See README.md for usage."
	# cd docs && make html

# Pre-commit hooks
pre-commit:
	@echo "Installing pre-commit hooks..."
	pre-commit install
	@echo "Pre-commit hooks installed!"

# Show version
version:
	@python -c "import sys; sys.path.insert(0, 'src'); from kryth import __version__; print(f'KRYTH version: {__version__}')" 2>/dev/null || \
		grep -E "^version\s*=" pyproject.toml | head -1 | cut -d'"' -f2

# Quick development cycle (format, lint, test)
dev-cycle: format lint test
	@echo "Development cycle complete!"

# Install with all optional dependencies
install-full: install
	uv pip install -e ".[bridge,docs,dev]"

# Create a new release (maintainers only)
release:
	@echo "Creating release..."
	@read -p "Enter version (e.g., 1.1.0): " version; \
		sed -i.bak "s/^version = .*/version = \"$$version\"/" pyproject.toml && \
		rm pyproject.toml.bak && \
		echo "Version updated to $$version. Don't forget to:" && \
		echo "  1. Update CHANGELOG.md" && \
		echo "  2. Commit changes" && \
		echo "  3. Create tag: git tag -a v$$version -m 'Release $$version'" && \
		echo "  4. Push tag: git push origin v$$version"

# Run KRYTH locally
run:
	@echo "Starting KRYTH..."
	kryth

# Run with debug logging
run-debug:
	@echo "Starting KRYTH with debug logging..."
	LOG_LEVEL=DEBUG kryth

# Check for outdated dependencies
outdated:
	uv pip list --outdated

# Update dependencies
update:
	@echo "Updating dependencies..."
	uv pip install -U --upgrade-strategy eager -e ".[bridge]"
	@echo "Dependencies updated!"

# Generate requirements.txt
requirements:
	@echo "Generating requirements.txt..."
	uv pip compile pyproject.toml -o requirements.txt --upgrade
	@echo "requirements.txt generated!"

# Backup sessions
backup-sessions:
	@echo "Backing up sessions to backup/..."
	mkdir -p backup
	cp -r ~/.kryth/sessions backup/ 2>/dev/null || echo "No sessions to backup"
	@echo "Backup complete!"

# Restore sessions
restore-sessions:
	@echo "Restoring sessions from backup/..."
	cp -r backup/sessions/* ~/.kryth/sessions/ 2>/dev/null || echo "No backup found"
	@echo "Restore complete!"