.PHONY: setup run stop restart clean self-update help format lint format-check install-hooks uninstall-hooks setup-full status logs update-tool-schemas

# Default target
help:
	@echo "Claude Code Telegram Bot - Complete Setup Guide"
	@echo ""
	@echo "Quick Start:"
	@echo "  make setup-full    - Complete setup (venv + dependencies + hooks)"
	@echo "  make run           - Run the bot"
	@echo "  make stop          - Stop the bot gracefully"
	@echo "  make restart       - Trigger bot restart in same terminal (via SIGUSR1)"
	@echo ""
	@echo "Development:"
	@echo "  make format        - Format all Python files with black and isort"
	@echo "  make lint          - Run linting checks with ruff"
	@echo "  make format-check  - Check if files are properly formatted"
	@echo ""
	@echo "Management:"
	@echo "  make status        - Check tmux session, hooks, and bot status"
	@echo "  make logs          - Tail bot logs with live updates"
	@echo "  make self-update   - Update the app from GitHub (git pull --rebase --autostash)"
	@echo "  make install-hooks - Install Claude Code hooks"
	@echo "  make uninstall-hooks - Uninstall Claude Code hooks"
	@echo "  make update-tool-schemas - Update tool schemas documentation from Claude JSONL files"
	@echo ""
	@echo "Other:"
	@echo "  make setup         - Create virtual environment and install dependencies"
	@echo "  make clean         - Remove virtual environment"
	@echo "  make help          - Show this help message"

setup:
	@echo "Setting up bot..."
	@./setup.sh

# Run default bot or specified bot (e.g., make run, make run 2, make run botprod)
run:
	@./run.sh $(filter-out $@,$(MAKECMDGOALS))

# Catch any additional arguments to 'make run'
%:
	@:

stop:
	@if [ -e "telegram-relay.sock" ]; then \
		echo "Stopping bot..."; \
		PID=$$(lsof -t telegram-relay.sock 2>/dev/null | head -1); \
		if [ -n "$$PID" ]; then \
			kill -TERM $$PID; \
			echo "✅ Sent shutdown signal to bot (PID: $$PID)"; \
			echo "   Waiting for graceful shutdown..."; \
		else \
			echo "⚠️  Socket file exists but no process found"; \
			echo "   Trying alternative method..."; \
			PID=$$(ps aux | grep "[p]ython src/main.py" | awk '{print $$2}'); \
			if [ -n "$$PID" ]; then \
				kill -TERM $$PID; \
				echo "✅ Sent shutdown signal to bot (PID: $$PID)"; \
			else \
				echo "❌ Could not find bot process"; \
				echo "   Manual cleanup: rm -f telegram-relay.sock"; \
			fi; \
		fi; \
	else \
		echo "ℹ️  Bot is not running"; \
	fi

restart:
	@if [ -e "telegram-relay.sock" ]; then \
		echo "🔄 Triggering bot restart..."; \
		PID=$$(lsof -t telegram-relay.sock 2>/dev/null | head -1); \
		if [ -n "$$PID" ]; then \
			echo "📤 Sending restart signal to bot (PID: $$PID)"; \
			echo "   The bot will:"; \
			echo "   1. Exit with code 42"; \
			echo "   2. Pull latest changes from GitHub"; \
			echo "   3. Restart automatically in the same terminal"; \
			kill -USR1 $$PID 2>/dev/null || kill -TERM $$PID; \
			echo "✅ Restart signal sent"; \
			echo "   Check the bot's terminal for progress"; \
		else \
			echo "⚠️  Socket file exists but no process found"; \
			echo "   Use 'make run' to start the bot"; \
		fi; \
	else \
		echo "ℹ️  Bot is not running"; \
		echo "   Use 'make run' to start the bot"; \
	fi

clean:
	@echo "Cleaning up..."
	@rm -rf venv
	@echo "✅ Virtual environment removed"

self-update:
	@echo "Updating from GitHub..."
	@git pull --rebase --autostash
	@echo "✅ Repository updated"
	@echo ""
	@if [ -e "telegram-relay.sock" ]; then \
		echo "⚠️  Bot is currently running."; \
		echo "   To apply changes, restart the bot:"; \
		echo "   - Stop: Ctrl+C or 'make stop'"; \
		echo "   - Start: 'make run'"; \
		echo "   Or use: 'make restart' for automatic restart"; \
	else \
		echo "ℹ️  Bot is not running. Start it with 'make run'"; \
	fi

format:
	@echo "Formatting Python files..."
	@./venv/bin/isort src/ hooks/ scripts/
	@./venv/bin/black src/ hooks/ scripts/
	@echo "✅ Code formatted"

lint:
	@echo "Running linting checks..."
	@./venv/bin/ruff check src/ hooks/ scripts/
	@echo "✅ Linting complete"

format-check:
	@echo "Checking code formatting..."
	@./venv/bin/black --check src/ hooks/ scripts/
	@./venv/bin/isort --check-only src/ hooks/ scripts/
	@./venv/bin/ruff check src/ hooks/ scripts/
	@echo "✅ Format check complete"

install-hooks:
	@echo "Installing Claude Code hooks..."
	@./venv/bin/python scripts/manage_hooks.py install
	@echo "✅ Hooks installation complete"

uninstall-hooks:
	@echo "Uninstalling Claude Code hooks..."
	@./venv/bin/python scripts/manage_hooks.py uninstall
	@echo "✅ Hooks uninstallation complete"

# Complete setup: dependencies + hooks + validation
setup-full: setup install-hooks
	@echo ""
	@echo "✅ Full setup complete!"
	@echo ""
	@echo "Next steps:"
	@echo "1. Edit .env file with your Telegram bot token and allowed users"
	@echo "2. Start Claude in a tmux session in your project directory"
	@echo "3. Run 'make run' to start the bot"

# Check system status
status:
	@echo "🔍 System Status Check"
	@echo "====================="
	@echo ""
	@echo "1. Virtual Environment:"
	@if [ -d "venv" ]; then \
		echo "   ✅ Virtual environment exists"; \
	else \
		echo "   ❌ Virtual environment not found (run 'make setup')"; \
	fi
	@echo ""
	@echo "2. Claude Hooks:"
	@if [ -f ~/.claude/settings.json ] && grep -q "telegram-claude-relay" ~/.claude/settings.json 2>/dev/null; then \
		echo "   ✅ Hooks are installed"; \
	else \
		echo "   ❌ Hooks not installed (run 'make install-hooks')"; \
	fi
	@echo ""
	@echo "3. Configuration:"
	@if [ -f ".env" ]; then \
		echo "   ✅ .env file exists"; \
		if grep -q "TELEGRAM_BOT_TOKEN=your_bot_token" .env 2>/dev/null; then \
			echo "   ⚠️  WARNING: Bot token not configured!"; \
		else \
			echo "   ✅ Bot token configured"; \
		fi; \
	else \
		echo "   ❌ .env file not found (copy from .env.example)"; \
	fi
	@echo ""
	@echo "4. Claude in tmux:"
	@found=0; \
	for session in $$(tmux list-sessions -F "#{session_name}" 2>/dev/null || echo ""); do \
		for pane in $$(tmux list-panes -t "$$session" -F "#{pane_id}" 2>/dev/null || echo ""); do \
			if tmux capture-pane -t "$$pane" -p 2>/dev/null | grep -q "Type a message"; then \
				echo "   ✅ Claude is running in tmux (session: $$session)"; \
				found=1; \
				break 2; \
			fi; \
		done; \
	done; \
	if [ $$found -eq 0 ]; then \
		echo "   ❌ Claude not found in any tmux session"; \
		echo "      Start Claude in tmux in your project directory"; \
	fi
	@echo ""
	@echo "5. Bot Process:"
	@if pgrep -f "python.*main.py" > /dev/null 2>&1; then \
		echo "   ✅ Bot is running (PID: $$(pgrep -f 'python.*main.py'))"; \
	else \
		echo "   ❌ Bot is not running (run 'make run')"; \
	fi
	@echo ""

# Tail bot logs with filtering
logs:
	@echo "📜 Tailing bot logs (Ctrl+C to stop)..."
	@echo "Filters: INFO and above, excluding DEBUG"
	@echo "=========================================="
	@tail -f telegram-claude-bot.log 2>/dev/null | grep -v "DEBUG" || echo "❌ Log file not found. Bot may not have been started yet."

# Update tool schemas documentation from Claude JSONL files
update-tool-schemas:
	@echo "📊 Updating tool schemas documentation..."
	@if [ -d "$(HOME)/.claude/projects" ]; then \
		./venv/bin/python scripts/analyze_tool_schemas.py --update --output tool_schemas.json --days 7; \
		echo "✅ Tool schemas updated from JSONL files: tool_schemas.json"; \
	else \
		echo "❌ Claude projects directory not found at $(HOME)/.claude/projects"; \
	fi