"""Application-wide constants."""

# Version info
APP_NAME = "Claude Code Telegram Bot"
APP_DESCRIPTION = "Telegram bot for remote Claude Code access"

# Default limits
DEFAULT_CLAUDE_TIMEOUT_SECONDS = 300

DEFAULT_RATE_LIMIT_REQUESTS = 10
DEFAULT_RATE_LIMIT_WINDOW = 60
DEFAULT_RATE_LIMIT_BURST = 20

# Session constants removed - using in-memory storage only

# Message limits
TELEGRAM_MAX_MESSAGE_LENGTH = 4096
SAFE_MESSAGE_LENGTH = 4000  # Leave room for formatting

# File upload constants removed - not supported in tmux mode
# Security patterns removed - handled by tmux isolation

# Database defaults removed - using in-memory storage only

# Claude Code defaults
DEFAULT_CLAUDE_BINARY = "claude"
DEFAULT_CLAUDE_OUTPUT_FORMAT = "stream-json"

# Logging
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
