# Claude Code Telegram Bot

A Telegram bot that acts as a relay between Telegram and Claude Code running in a tmux pane.

## What it does

- Receives messages from Telegram
- Forwards them to Claude Code in a tmux pane
- Returns Claude's responses back to Telegram
- That's it! Pure message relay, no fancy features.

## Setup

### 1. Prerequisites

- Python 3.8+
- `tmux` installed
- Claude Code CLI installed and authenticated
- Telegram bot token from [@BotFather](https://t.me/BotFather)

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
TMUX_PANE=

# Option B: Specify exact pane (format: session:window.pane)
TMUX_PANE=claude-session:0.0
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
5. Claude's response comes back to you

**The bot is just a relay** - it forwards your Telegram messages to Claude Code running in tmux and returns the responses.

## Configuration

- `TELEGRAM_BOT_TOKEN` - Your bot token
- `TELEGRAM_BOT_USERNAME` - Your bot username
- `ALLOWED_USERS` - Comma-separated Telegram user IDs
- `TMUX_PANE` - Target tmux pane (format: `session:window.pane`)
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
3. Captures Claude's response via `tmux capture-pane`
4. Parses and returns clean response to Telegram

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

## License

MIT