# Project Context: Telegram Bot with tmux Integration

## Current Project

A Telegram bot that provides remote access to Claude Code CLI with advanced hook integration. Users send messages via Telegram, and receive real-time tool notifications and live status updates as Claude processes their requests.

### ✅ File Structure (Actual)

```
src/
├── __init__.py
├── main.py                    # Application entry point
├── exceptions.py              # Global exception definitions
├── bot/
│   ├── __init__.py
│   ├── core.py                # Main bot orchestrator
│   ├── handlers/
│   │   ├── __init__.py
│   │   ├── command.py         # /start command only
│   │   ├── message.py         # Text message handling only
│   │   └── webhook.py         # Hook monitoring webhook handler
│   ├── middleware/
│   │   ├── __init__.py
│   │   ├── auth.py            # Authentication middleware
│   │   └── rate_limit.py      # Rate limiting middleware
│   └── utils/                 # Bot utilities
│       ├── __init__.py
│       ├── formatting.py      # Response formatting
│       └── message_sender.py  # Robust message sender with fallback
├── claude/
│   ├── __init__.py
│   ├── facade.py              # Main Claude integration facade
│   ├── tmux_integration.py    # tmux mode implementation
│   ├── responses.py           # ClaudeResponse, StreamUpdate data structures
│   ├── conversation_monitor.py # Conversation monitoring
│   ├── unix_socket_server.py  # Unix socket server for hooks
│   └── exceptions.py          # Claude-specific errors
├── tmux/
│   ├── __init__.py
│   ├── client.py              # TmuxClient for pane communication
│   └── exceptions.py          # TmuxCommandError, TmuxPaneNotFoundError
├── config/
│   ├── __init__.py
│   ├── settings.py            # Pydantic settings (full config with many options)
│   └── loader.py              # Configuration loading
├── security/
│   ├── __init__.py
│   ├── auth.py                # WhitelistAuthProvider, AuthenticationManager
│   └── rate_limiter.py        # RateLimiter implementation
└── utils/
    ├── __init__.py
    └── constants.py           # Application constants
```

### ✅ Features Actually Implemented

1. ✅ **Minimal Bot Interface**: Only `/start` command and text message handling
2. ✅ **Direct Message Relay**: All text messages forwarded directly to Claude via tmux
3. ✅ **Smart Response Parsing**: Extracts only new Claude responses from tmux output
4. ✅ **Auto-Discovery**: Can find Claude pane automatically or use configured target
5. ✅ **Security Middleware**: Authentication, rate limiting, security validation (all in-memory)
6. ✅ **Error Handling**: tmux error handling and user-friendly error messages
7. ✅ **Progress Indicators**: Shows typing indicators and progress messages during processing
8. ✅ **Hook Integration**: Real-time tool notifications and permission dialogs via Unix socket
9. ✅ **Professional Markdown Handling**: Uses telegramify-markdown with three-tier fallback system
10. ✅ **Robust Message Delivery**: Guarantees 100% message delivery (MarkdownV2 → HTML → Plain text)

### ✅ Configuration (Full Settings Available)

```bash
# Required settings in .env
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_BOT_USERNAME=your_bot_username
ALLOWED_USERS=123456789,987654321

# tmux settings (only required config)
TMUX_PANE=                     # Optional - auto-discovery if empty

# Optional (many more available in settings.py)
DEBUG=false

```

### Limitations

- Never add credentials or sensitive information to the codebase.
- Never include real user IDs or sensitive data in the codebase, including paths, web searches, etc.
- Never edit pyproject.toml
