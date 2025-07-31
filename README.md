# Claude Code Telegram Bot

A Telegram bot that acts as a relay between Telegram and Claude Code running in a tmux pane.

## What it does

- Receives messages from Telegram
- Forwards them to Claude Code in a tmux pane
- Track Claude activity (including thinking steps) via hooks
- Returns Claude's responses back to Telegram

## Setup

### 1. Prerequisites

- Python 3.8+
- `tmux` installed
- Claude Code CLI installed and authenticated
- Telegram bot token from [@BotFather](https://t.me/BotFather)
- Claude Hooks set in ~/.claude/settings.json

### 2. Start Claude in tmux (REQUIRED FIRST)

**You MUST start Claude Code in tmux before running the bot.** The bot connects to Claude running in a tmux pane.

### 3. Configure the bot

```bash
# Copy environment template
cp .env.example .env

# Edit .env with your settings
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_BOT_USERNAME=your_bot_username
ALLOWED_USERS=123456789,987654321

# Option A: Let bot auto-discover Claude pane (recommended)
PANE=

# Option B: Specify exact pane (format: session:window.pane)
PANE=claude-session:0.0
```

### 4. Install and run

```bash
# Setup (creates venv, installs dependencies)
make setup

# Run the bot
make run
```

## Usage

**Important: Follow this order every time!**

1. **First**: Start Claude in tmux (see step 2 above)
2. **Then**: Start the bot with `make run`
3. Message the bot on Telegram
4. Your message goes directly to Claude in tmux
5. Claude's response comes back with live status updates showing tools being used

The bot uses Claude Code hooks to provide real-time tool notifications and live-updating status messages.

## Configuration

- `TELEGRAM_BOT_TOKEN` - Your bot token
- `TELEGRAM_BOT_USERNAME` - Your bot username
- `ALLOWED_USERS` - Comma-separated Telegram user IDs
- `PANE` - Target tmux pane (format: `session:window.pane`)
- `DEBUG` - Enable debug logging (optional)

## Commands

```bash
make setup    # Create venv and install dependencies
make run      # Run the bot
make clean    # Remove venv
make help     # Show available commands
```

## How it works

1. Bot receives Telegram message
2. Sends message to tmux pane via `tmux send-keys`
3. Claude Code triggers hooks to send updates back to the bot
4. Bot processes updates and sends them back to Telegram
5. You see Claude's responses and tool usage in real-time

### Claude Code Hooks Integration

Track ALL Claude activity including thinking steps:

1. **Setup Claude Code hooks**:
   - Create or edit `~/.claude/settings.json` based on `claude-code-settings.json`

2. **How it works**:
   - Claude Code Stop hook triggers after each response
   - Hook sends data via secure Unix socket at `~/.claude/telegram-relay.sock`
   - Bot receives updates and sends to Telegram

3. **What you'll see**:
   - 💭 Thinking steps (Claude's internal reasoning)
   - 🔧 Tool usage (file edits, commands, etc.)
   - 🤖 Regular responses
   - Full transparency into Claude's process

## Troubleshooting

### Claude not running in tmux (most common issue)

```bash
# Check if tmux sessions exist
tmux list-sessions

# Check if Claude is running in a specific session
tmux capture-pane -t session-name -p

# If no Claude session, create one:
tmux new-session -d -s claude-session
tmux send-keys -t claude-session "claude" Enter
```

### Bot won't start

- Check `.env` file exists and has correct values
- Run `make setup` to reinstall dependencies
- Verify Telegram bot token is valid

### No responses from bot

- **First check**: Is Claude actually running in tmux?
- Check bot logs for connection errors
- Verify your Telegram user ID is in `ALLOWED_USERS`
- Test Claude directly in tmux: `tmux send-keys -t your-pane "test message" Enter`

## TODO

### Hook Setup Automation
- [ ] **Hook Install/Uninstall**: Create `make install-hooks` and `make uninstall-hooks` commands
  - Automatically backup existing `~/.claude/settings.json`
  - Create `~/.claude/settings.json` if not exists
  - Install hooks from `claude-code-settings.json` template with real paths to python scripts
  - Restore original settings on uninstall
  - Merge new hooks with existing hooks
  - Hooks should be installed BEFORE starting the Claude

### Event Filtering
- [ ] **tmux Session Filtering**: Only process hooks from the specific tmux session
  - Add session ID to hook payloads
  - Filter out hooks from other Claude instances
  - Prevent cross-session message contamination
  - Support multiple bots with different tmux sessions

### Enhanced Integration
- [ ] **Smart Hook Detection**: Auto-detect if hooks are properly configured
  - Check `~/.claude/settings.json` on startup
  - Warn user if hooks are missing or misconfigured
  - Provide guided setup instructions

### Tool Usage Transparency
- [ ] **Detailed Tool Messages**: Enhanced pre/post tool notifications
  - Show which specific tool is being used (Read, Write, Bash, etc.)
  - Display file paths for file operations (created, modified, deleted)
  - Show command details for Bash tool usage
  - Include execution time and success/failure status
  - Format tool parameters in user-friendly way

### Interactive Permission Handling
- [ ] **Claude Permission Requests**: Handle Claude's permission/confirmation prompts
  - Detect when Claude asks for user confirmation
  - Send Telegram inline keyboard buttons for 1,2,3 responses
  - Forward user selections back to Claude automatically
  - Show clear context of what Claude is asking permission for

### Code Formatting & Quality
- [ ] **Python Code Formatting**: Set up automated code formatting
  - Add `black` for consistent Python code formatting
  - Add `isort` for import sorting
  - Add `flake8` or `ruff` for linting
  - Create `make format` and `make lint` commands
  - Add pre-commit hooks for automatic formatting
  - Configure formatting rules in `pyproject.toml`

### Makefile Enhancements
- [ ] **Integrated Workflow**: Streamline the entire setup process
  - `make setup-full`: Install hooks + dependencies + validate configuration
  - `make start-claude`: Start Claude in tmux automatically
  - `make status`: Check tmux session, hooks, and bot status
  - `make logs`: Tail bot logs with filtering
  - `make format`: Format all Python files
  - `make lint`: Run linting checks

## License

MIT