# Project Context: Telegram Bot with tmux Integration

## Current Project

A Telegram bot that provides remote access to Claude Code CLI via tmux relay. Users send messages via Telegram, bot forwards them to Claude running in a tmux pane, and returns responses.

## ✅ COMPLETED: Current Architecture

- **Telegram Bot**: Full-featured bot with comprehensive middleware
- **Claude Integration**: tmux-only mode - direct communication with Claude running in tmux pane
- **Security**: Whitelist authentication, rate limiting, audit logging, security validators
- **Features**: Text message relay only

## ✅ IMPLEMENTED: tmux Integration

Direct communication with Claude Code running in a tmux pane.

### ✅ Implementation Complete

- ✅ Configure target pane: `TMUX_PANE="session:window.pane"` or auto-discovery
- ✅ Send prompts: `tmux send-keys -t pane "prompt" && tmux send-keys -t pane Enter`
- ✅ Capture responses: `tmux capture-pane -t pane -S -100 -p` (default 100 lines)
- ✅ Parse tmux output to extract clean Claude responses
- ✅ Track previous output to show only new content

### ✅ File Structure (Actual)

```
src/
├── bot/
│   ├── core.py                  # Main bot orchestrator
│   ├── handlers/
│   │   ├── command.py          # /start command only
│   │   └── message.py          # Text message handling only
│   ├── middleware/             # Auth, rate limiting, security validation
│   │   ├── auth.py
│   │   ├── rate_limit.py
│   │   └── security.py
│   └── utils/formatting.py    # Response formatting
├── claude/
│   ├── facade.py              # Main Claude integration facade
│   ├── tmux_integration.py    # tmux mode implementation
│   ├── parser.py              # tmux output parsing with ResponseFormatter
│   ├── responses.py           # ClaudeResponse, StreamUpdate data structures
│   ├── monitor.py             # Claude monitoring
│   └── exceptions.py          # Claude-specific errors
├── tmux/
│   ├── client.py              # TmuxClient for pane communication
│   └── exceptions.py          # TmuxCommandError, TmuxPaneNotFoundError
├── config/
│   ├── settings.py            # Pydantic settings (full config with many options)
│   └── loader.py              # Configuration loading
├── security/
│   ├── auth.py                # WhitelistAuthProvider, TokenAuthProvider
│   ├── audit.py               # AuditLogger with InMemoryAuditStorage
│   ├── rate_limiter.py        # RateLimiter implementation
│   └── validators.py          # SecurityValidator
└── utils/constants.py         # Application constants
```

### ✅ Features Actually Implemented

1. ✅ **Minimal Bot Interface**: Only `/start` command and text message handling
2. ✅ **Direct Message Relay**: All text messages forwarded directly to Claude via tmux
3. ✅ **Smart Response Parsing**: Extracts only new Claude responses from tmux output
4. ✅ **Auto-Discovery**: Can find Claude pane automatically or use configured target
5. ✅ **Security Middleware**: Authentication, rate limiting, security validation, audit logging (all in-memory)
6. ✅ **Error Handling**: tmux error handling and user-friendly error messages
7. ✅ **Progress Indicators**: Shows typing indicators and progress messages during processing

### ✅ Configuration (Full Settings Available)

```bash
# Required settings in .env
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_BOT_USERNAME=your_bot_username
ALLOWED_USERS=123456789,987654321

# tmux settings (only required config)
TMUX_PANE=                     # Optional - auto-discovery if empty
TMUX_CAPTURE_LINES=100         # Default 100 lines
TMUX_POLL_INTERVAL=1.0         # Default 1.0 seconds

# Optional (many more available in settings.py)
DEBUG=false
```

### ✅ Usage Flow (Working)

1. **Setup**: Start Claude in tmux: `tmux new-session -d -s claude-session && tmux send-keys -t claude-session "claude" Enter`
2. **Bot**: Start bot with `make run`
3. **Message Flow**:
   - User sends text message → tmux pane receives message via `tmux send-keys`
   - Claude responds → Bot captures response via `tmux capture-pane` → User gets clean response
   - **No file uploads supported** (tmux send-keys only works with text)

## Current Status: PRODUCTION READY ✅

- ✅ **Fully Functional**: tmux integration working perfectly
- ✅ **Core Features Working**: Handles text messages only, comprehensive error handling
- ✅ **Clean Codebase**: All outdated comments removed, code simplified
- ✅ **Robust Architecture**: Full middleware stack with security and monitoring
- ✅ **Well Documented**: Clear README with setup instructions
- ✅ **In-Memory Only**: No database dependencies, uses InMemoryAuditStorage

The bot provides a Telegram interface to Claude Code via tmux, with text message relay only, comprehensive error handling, and security middleware. File uploads are not supported due to tmux architectural limitations.