"""Security middleware for input validation and threat detection."""

from typing import Any, Callable, Dict

import structlog

logger = structlog.get_logger()


async def security_middleware(
    handler: Callable, event: Any, data: Dict[str, Any]
) -> Any:
    """Validate inputs and detect security threats.

    This middleware:
    1. Validates message content for dangerous patterns
    2. Sanitizes file uploads
    3. Detects potential attacks
    4. Logs security violations
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

    # Validate text content if present
    message = event.effective_message
    if message and message.text:
        is_safe, violation_type = await validate_message_content(
            message.text, user_id
        )
        if not is_safe:
            await message.reply_text(
                f"🛡️ **Security Alert**\n\n"
                f"Your message contains potentially dangerous content and has been blocked.\n"
                f"Violation: {violation_type}\n\n"
                "If you believe this is an error, please contact the administrator."
            )
            return  # Block processing

    # File upload validation removed - not supported in tmux mode

    # Log successful security validation
    logger.debug(
        "Security validation passed",
        user_id=user_id,
        username=username,
        has_text=bool(message and message.text),
        # has_document tracking removed - not supported in tmux mode
    )

    # Continue to handler
    return await handler(event, data)


async def validate_message_content(
    text: str, user_id: int
) -> tuple[bool, str]:
    """Validate message text content for security threats."""

    # Check for command injection patterns
    dangerous_patterns = [
        r";\s*rm\s+",
        r";\s*del\s+",
        r";\s*format\s+",
        r"`[^`]*`",
        r"\$\([^)]*\)",
        r"&&\s*rm\s+",
        r"\|\s*mail\s+",
        r">\s*/dev/",
        r"curl\s+.*\|\s*sh",
        r"wget\s+.*\|\s*sh",
        r"exec\s*\(",
        r"eval\s*\(",
    ]

    import re

    for pattern in dangerous_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            logger.warning(
                "Command injection attempt detected",
                user_id=user_id,
                pattern=pattern,
                text_preview=text[:100],
            )
            return False, "Command injection attempt"

    # Check for path traversal attempts
    path_traversal_patterns = [
        r"\.\./.*",
        r"~\/.*",
        r"\/etc\/.*",
        r"\/var\/.*",
        r"\/usr\/.*",
        r"\/sys\/.*",
        r"\/proc\/.*",
    ]

    for pattern in path_traversal_patterns:
        if re.search(pattern, text):
            logger.warning(
                "Path traversal attempt detected",
                user_id=user_id,
                pattern=pattern,
                text_preview=text[:100],
            )
            return False, "Path traversal attempt"

    # Check for suspicious URLs or domains
    suspicious_patterns = [
        r"https?://[^/]*\.ru/",
        r"https?://[^/]*\.tk/",
        r"https?://[^/]*\.ml/",
        r"https?://bit\.ly/",
        r"https?://tinyurl\.com/",
        r"javascript:",
        r"data:text/html",
    ]

    for pattern in suspicious_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            logger.warning("Suspicious URL detected", user_id=user_id, pattern=pattern)
            return False, "Suspicious URL detected"

    return True, ""


# File upload validation removed - not supported in tmux mode


async def threat_detection_middleware(
    handler: Callable, event: Any, data: Dict[str, Any]
) -> Any:
    """Advanced threat detection middleware.

    This middleware looks for patterns that might indicate
    sophisticated attacks or reconnaissance attempts.
    """
    user_id = event.effective_user.id if event.effective_user else None
    if not user_id:
        return await handler(event, data)


    # Track user behavior patterns
    user_behavior = data.setdefault("user_behavior", {})
    user_data = user_behavior.setdefault(
        user_id,
        {
            "message_count": 0,
            "failed_commands": 0,
            "path_requests": 0,
            # "file_requests" removed - not supported in tmux mode
            "first_seen": None,
        },
    )

    import time

    current_time = time.time()

    if user_data["first_seen"] is None:
        user_data["first_seen"] = current_time

    user_data["message_count"] += 1

    # Check for reconnaissance patterns
    message = event.effective_message
    text = message.text if message else ""

    # Suspicious commands that might indicate reconnaissance
    recon_patterns = [
        r"ls\s+/",
        r"find\s+/",
        r"locate\s+",
        r"which\s+",
        r"whereis\s+",
        r"ps\s+",
        r"netstat\s+",
        r"lsof\s+",
        r"env\s*$",
        r"printenv\s*$",
        r"whoami\s*$",
        r"id\s*$",
        r"uname\s+",
        r"cat\s+/etc/",
        r"cat\s+/proc/",
    ]

    import re

    recon_attempts = sum(
        1 for pattern in recon_patterns if re.search(pattern, text, re.IGNORECASE)
    )

    if recon_attempts > 0:
        user_data["recon_attempts"] = (
            user_data.get("recon_attempts", 0) + recon_attempts
        )

        # Alert if too many reconnaissance attempts
        if user_data["recon_attempts"] > 5:
            logger.warning(
                "Reconnaissance attempt pattern detected",
                user_id=user_id,
                total_attempts=user_data["recon_attempts"],
                current_message=text[:100],
            )

            if event.effective_message:
                await event.effective_message.reply_text(
                    "🔍 **Suspicious Activity Detected**\n\n"
                    "Multiple reconnaissance-style commands detected. "
                    "This activity has been logged.\n\n"
                    "If you have legitimate needs, please contact the administrator."
                )

    return await handler(event, data)
