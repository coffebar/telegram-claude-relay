.PHONY: setup run clean help

# Default target
help:
	@echo "Claude Code Telegram Bot - Simple Setup"
	@echo ""
	@echo "Available commands:"
	@echo "  make setup   - Create virtual environment and install dependencies"
	@echo "  make run     - Run the bot"
	@echo "  make clean   - Remove virtual environment"
	@echo "  make help    - Show this help message"

setup:
	@echo "Setting up bot..."
	@./setup.sh

run:
	@./run.sh

clean:
	@echo "Cleaning up..."
	@rm -rf venv
	@echo "✅ Virtual environment removed"