"""Security framework for Claude Code Telegram Bot.

This module provides comprehensive security features including:
- Multi-layer authentication (whitelist and token-based)
- Rate limiting with token bucket algorithm

Key Components:
- AuthenticationManager: Main authentication system
- RateLimiter: Request and cost-based rate limiting
"""

from .auth import (
    AuthenticationManager,
    AuthProvider,
    UserSession,
    WhitelistAuthProvider,
)
from .rate_limiter import RateLimitBucket, RateLimiter

__all__ = [
    "AuthProvider",
    "WhitelistAuthProvider",
    "AuthenticationManager",
    "UserSession",
    "RateLimiter",
    "RateLimitBucket",
]
