.PHONY: setup run clean help format lint format-check

# Default target
help:
	@echo "Claude Code Telegram Bot - Simple Setup"
	@echo ""
	@echo "Available commands:"
	@echo "  make setup       - Create virtual environment and install dependencies"
	@echo "  make run         - Run the bot"
	@echo "  make clean       - Remove virtual environment"
	@echo "  make format      - Format all Python files with black and isort"
	@echo "  make lint        - Run linting checks with ruff"
	@echo "  make format-check- Check if files are properly formatted"
	@echo "  make help        - Show this help message"

setup:
	@echo "Setting up bot..."
	@./setup.sh

run:
	@./run.sh

clean:
	@echo "Cleaning up..."
	@rm -rf venv
	@echo "✅ Virtual environment removed"

format:
	@echo "Formatting Python files..."
	@./venv/bin/isort src/ hooks/
	@./venv/bin/black src/ hooks/
	@echo "✅ Code formatted"

lint:
	@echo "Running linting checks..."
	@./venv/bin/ruff check src/ hooks/
	@echo "✅ Linting complete"

format-check:
	@echo "Checking code formatting..."
	@./venv/bin/black --check src/ hooks/
	@./venv/bin/isort --check-only src/ hooks/
	@./venv/bin/ruff check src/ hooks/
	@echo "✅ Format check complete"