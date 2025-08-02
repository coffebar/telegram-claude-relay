"""Rate limiting implementation with multiple strategies.

Features:
- Token bucket algorithm
- Cost-based limiting
- Per-user tracking
- Burst handling
"""

import asyncio

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

import structlog

from ..config.settings import Settings


logger = structlog.get_logger()


@dataclass
class RateLimitBucket:
    """Token bucket for rate limiting."""

    capacity: int
    tokens: float
    last_update: datetime
    refill_rate: float = 1.0  # tokens per second

    def consume(self, tokens: int = 1) -> bool:
        """Try to consume tokens from bucket."""
        self._refill()
        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False

    def _refill(self) -> None:
        """Refill tokens based on time passed."""
        now = datetime.utcnow()
        elapsed = (now - self.last_update).total_seconds()
        self.tokens = min(self.capacity, self.tokens + (elapsed * self.refill_rate))
        self.last_update = now

    def get_wait_time(self, tokens: int = 1) -> float:
        """Get time to wait before tokens are available."""
        self._refill()
        if self.tokens >= tokens:
            return 0.0

        tokens_needed = tokens - self.tokens
        return tokens_needed / self.refill_rate

    def get_status(self) -> Dict[str, float]:
        """Get current bucket status."""
        self._refill()
        return {
            "capacity": self.capacity,
            "tokens": self.tokens,
            "utilization": (self.capacity - self.tokens) / self.capacity,
            "refill_rate": self.refill_rate,
        }


class RateLimiter:
    """Request-based rate limiting system."""

    def __init__(self, config: Settings):
        self.config = config
        self.request_buckets: Dict[int, RateLimitBucket] = {}
        self.locks: Dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

        # Calculate refill rate from config
        self.refill_rate = (
            self.config.rate_limit_requests / self.config.rate_limit_window
        )

        logger.info(
            "Rate limiter initialized",
            requests_per_window=self.config.rate_limit_requests,
            window_seconds=self.config.rate_limit_window,
            burst_capacity=self.config.rate_limit_burst,
            refill_rate=self.refill_rate,
        )

    async def check_rate_limit(
        self, user_id: int, tokens: int = 1
    ) -> Tuple[bool, Optional[str]]:
        """Check if request is allowed under rate limits."""
        async with self.locks[user_id]:
            # Check request rate limit
            rate_allowed, rate_message = self._check_request_rate(user_id, tokens)
            if not rate_allowed:
                logger.warning(
                    "Request rate limit exceeded",
                    user_id=user_id,
                    tokens_requested=tokens,
                )
                return False, rate_message

            # Consume tokens
            self._consume_request_tokens(user_id, tokens)

            logger.debug("Rate limit check passed", user_id=user_id, tokens=tokens)
            return True, None

    def _check_request_rate(
        self, user_id: int, tokens: int
    ) -> Tuple[bool, Optional[str]]:
        """Check request rate limit."""
        bucket = self._get_or_create_bucket(user_id)

        if bucket.consume(tokens):
            return True, None

        wait_time = bucket.get_wait_time(tokens)
        status = bucket.get_status()

        message = (
            f"Rate limit exceeded. Please wait {wait_time:.1f} seconds "
            f"before making more requests. "
            f"Bucket: {status['tokens']:.1f}/{status['capacity']} tokens available."
        )
        return False, message

    def _consume_request_tokens(self, user_id: int, tokens: int) -> None:
        """Consume tokens from request bucket."""
        bucket = self._get_or_create_bucket(user_id)
        bucket.consume(tokens)

    def _get_or_create_bucket(self, user_id: int) -> RateLimitBucket:
        """Get or create rate limit bucket for user."""
        if user_id not in self.request_buckets:
            self.request_buckets[user_id] = RateLimitBucket(
                capacity=self.config.rate_limit_burst,
                tokens=self.config.rate_limit_burst,
                last_update=datetime.utcnow(),
                refill_rate=self.refill_rate,
            )
            self._limit_dict_size()
            logger.debug("Created rate limit bucket", user_id=user_id)

        return self.request_buckets[user_id]

    async def reset_user_limits(self, user_id: int) -> None:
        """Reset limits for a user (admin function)."""
        async with self.locks[user_id]:
            # Reset request bucket
            if user_id in self.request_buckets:
                self.request_buckets[user_id].tokens = self.request_buckets[
                    user_id
                ].capacity
                self.request_buckets[user_id].last_update = datetime.utcnow()

            logger.info("User limits reset", user_id=user_id)

    def get_user_status(self, user_id: int) -> Dict[str, Any]:
        """Get current rate limit status for user."""
        # Get request bucket status
        bucket = self._get_or_create_bucket(user_id)
        bucket_status = bucket.get_status()

        return {
            "request_bucket": bucket_status,
        }

    def get_global_status(self) -> Dict[str, Any]:
        """Get global rate limiter statistics."""
        return {
            "active_users": len(self.request_buckets),
            "config": {
                "requests_per_window": self.config.rate_limit_requests,
                "window_seconds": self.config.rate_limit_window,
                "burst_capacity": self.config.rate_limit_burst,
                "refill_rate": self.refill_rate,
            },
        }

    async def cleanup_inactive_users(
        self, inactive_threshold: timedelta = timedelta(hours=24)
    ) -> int:
        """Clean up rate limit data for inactive users."""
        now = datetime.utcnow()
        inactive_users = []

        # Find users with old buckets
        for user_id, bucket in self.request_buckets.items():
            if now - bucket.last_update > inactive_threshold:
                inactive_users.append(user_id)

        # Clean up data
        for user_id in inactive_users:
            self.request_buckets.pop(user_id, None)
            self.locks.pop(user_id, None)

        if inactive_users:
            logger.info(
                "Cleaned up inactive users",
                count=len(inactive_users),
                threshold_hours=inactive_threshold.total_seconds() / 3600,
            )

        return len(inactive_users)

    def _limit_dict_size(self, max_size: int = 1000) -> None:
        """Remove oldest entries if dictionaries exceed max_size."""
        if len(self.request_buckets) <= max_size:
            return

        # Sort by last_update timestamp to remove oldest
        sorted_buckets = sorted(
            self.request_buckets.items(), key=lambda x: x[1].last_update
        )

        # Remove oldest entries to get back to max_size
        entries_to_remove = len(self.request_buckets) - max_size
        for i in range(entries_to_remove):
            user_id_to_remove = sorted_buckets[i][0]
            self.request_buckets.pop(user_id_to_remove, None)
            self.locks.pop(user_id_to_remove, None)
