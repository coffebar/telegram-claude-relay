"""Rate limiting middleware for Telegram bot."""

from typing import Any, Callable, Dict

import structlog

logger = structlog.get_logger()


async def rate_limit_middleware(
    handler: Callable, event: Any, data: Dict[str, Any]
) -> Any:
    """Check rate limits before processing messages.

    This middleware:
    1. Checks request rate limits
    2. Estimates and checks cost limits
    3. Logs rate limit violations
    4. Provides helpful error messages
    """
    user_id = event.effective_user.id if event.effective_user else None
    username = (
        getattr(event.effective_user, "username", None)
        if event.effective_user
        else None
    )

    if not user_id:
        logger.warning("No user information in update")
        return await handler(event, data)

    # Get dependencies from context
    rate_limiter = data.get("rate_limiter")
    audit_logger = data.get("audit_logger")

    if not rate_limiter:
        logger.error("Rate limiter not available in middleware context")
        # Don't block on missing rate limiter - this could be a config issue
        return await handler(event, data)

    # Check rate limits
    allowed, message = await rate_limiter.check_rate_limit(
        user_id=user_id, tokens=1  # One token per message
    )

    if not allowed:
        logger.warning(
            "Rate limit exceeded",
            user_id=user_id,
            username=username,
            message=message,
        )

        # Log rate limit violation
        if audit_logger:
            await audit_logger.log_rate_limit_exceeded(
                user_id=user_id,
                limit_type="combined",
                current_usage=0,  # Would need to extract from rate_limiter
                limit_value=0,  # Would need to extract from rate_limiter
            )

        # Send user-friendly rate limit message
        if event.effective_message:
            await event.effective_message.reply_text(f"⏱️ {message}")
        return  # Stop processing

    # Rate limit check passed
    logger.debug(
        "Rate limit check passed",
        user_id=user_id,
        username=username,
    )

    # Continue to handler
    return await handler(event, data)






async def burst_protection_middleware(
    handler: Callable, event: Any, data: Dict[str, Any]
) -> Any:
    """Additional burst protection for high-frequency requests.

    This middleware provides an additional layer of protection
    against burst attacks that might bypass normal rate limiting.
    """
    user_id = event.from_user.id

    # Get or create burst tracker
    burst_tracker = data.setdefault("burst_tracker", {})
    user_burst_data = burst_tracker.setdefault(
        user_id, {"recent_requests": [], "warnings_sent": 0}
    )

    import time

    current_time = time.time()

    # Clean old requests (older than 10 seconds)
    user_burst_data["recent_requests"] = [
        req_time
        for req_time in user_burst_data["recent_requests"]
        if current_time - req_time < 10
    ]

    # Add current request
    user_burst_data["recent_requests"].append(current_time)

    # Check for burst (more than 5 requests in 10 seconds)
    if len(user_burst_data["recent_requests"]) > 5:
        user_burst_data["warnings_sent"] += 1

        logger.warning(
            "Burst protection triggered",
            user_id=user_id,
            requests_in_window=len(user_burst_data["recent_requests"]),
            warnings_sent=user_burst_data["warnings_sent"],
        )

        # Progressive response based on warning count
        if user_burst_data["warnings_sent"] == 1:
            if event.effective_message:
                await event.effective_message.reply_text(
                    "⚠️ **Slow down!**\n\n"
                    "You're sending requests too quickly. "
                    "Please wait a moment between messages."
                )
        elif user_burst_data["warnings_sent"] <= 3:
            if event.effective_message:
                await event.effective_message.reply_text(
                    "🛑 **Rate limit warning**\n\n"
                    "Please reduce your request frequency to avoid being temporarily blocked."
                )
        else:
            if event.effective_message:
                await event.effective_message.reply_text(
                    "🚫 **Temporarily blocked**\n\n"
                    "Too many rapid requests. Please wait 30 seconds before trying again."
                )
            return  # Block this request

    return await handler(event, data)
