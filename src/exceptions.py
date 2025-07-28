"""Custom exceptions for Claude Code Telegram Bot."""


class ClaudeCodeTelegramError(Exception):
    """Base exception for Claude Code Telegram Bot."""

    pass


class ConfigurationError(ClaudeCodeTelegramError):
    """Configuration-related errors."""

    pass


class MissingConfigError(ConfigurationError):
    """Required configuration is missing."""

    pass


class InvalidConfigError(ConfigurationError):
    """Configuration is invalid."""

    pass


class SecurityError(ClaudeCodeTelegramError):
    """Security-related errors."""

    pass


class AuthenticationError(SecurityError):
    """Authentication failed."""

    pass


class AuthorizationError(SecurityError):
    """Authorization failed."""

    pass


class DirectoryTraversalError(SecurityError):
    """Directory traversal attempt detected."""

    pass


class ClaudeError(ClaudeCodeTelegramError):
    """Claude Code-related errors."""

    pass


class ClaudeTimeoutError(ClaudeError):
    """Claude Code operation timed out."""

    pass


class ClaudeProcessError(ClaudeError):
    """Claude Code process execution failed."""

    pass


class ClaudeParsingError(ClaudeError):
    """Failed to parse Claude Code output."""

    pass


class StorageError(ClaudeCodeTelegramError):
    """Storage-related errors."""

    pass


class DatabaseConnectionError(StorageError):
    """Database connection failed."""

    pass


class DataIntegrityError(StorageError):
    """Data integrity check failed."""

    pass


class TelegramError(ClaudeCodeTelegramError):
    """Telegram API-related errors."""

    pass


class MessageTooLongError(TelegramError):
    """Message exceeds Telegram's length limit."""

    pass


class RateLimitError(TelegramError):
    """Rate limit exceeded."""

    pass


class RateLimitExceeded(RateLimitError):
    """Rate limit exceeded (alias for compatibility)."""

    pass
