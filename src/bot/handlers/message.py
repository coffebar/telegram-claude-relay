"""Message handlers for non-command inputs."""

from typing import Optional

import structlog
from telegram import Update
from telegram.ext import ContextTypes

from ...claude.exceptions import ClaudeToolValidationError
from ...config.settings import Settings
from ...security.rate_limiter import RateLimiter

logger = structlog.get_logger()


async def _safe_reply_text(update, text, parse_mode=None, reply_markup=None, reply_to_message_id=None):
    """Send message with markdown fallback to plain text if parsing fails."""
    try:
        await update.message.reply_text(
            text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            reply_to_message_id=reply_to_message_id,
        )
    except Exception as e:
        # If markdown parsing failed, try sending as plain text
        if parse_mode and ("parse entities" in str(e).lower() or "can't parse" in str(e).lower()):
            logger.warning("Markdown parsing failed, retrying as plain text", error=str(e))
            try:
                await update.message.reply_text(
                    text,
                    reply_markup=reply_markup,
                    reply_to_message_id=reply_to_message_id,
                )
                return
            except Exception as e2:
                logger.error("Failed to send as plain text too", error=str(e2))
        # Re-raise the original error if it's not a parsing issue
        raise


async def _format_progress_update(update_obj) -> Optional[str]:
    """Format progress updates with enhanced context and visual indicators."""
    if update_obj.type == "tool_result":
        # Show tool completion status
        tool_name = "Unknown"
        if update_obj.metadata and update_obj.metadata.get("tool_use_id"):
            # Try to extract tool name from context if available
            tool_name = update_obj.metadata.get("tool_name", "Tool")

        if update_obj.is_error():
            return f"❌ **{tool_name} failed**\n\n_{update_obj.get_error_message()}_"
        else:
            execution_time = ""
            if update_obj.metadata and update_obj.metadata.get("execution_time_ms"):
                time_ms = update_obj.metadata["execution_time_ms"]
                execution_time = f" ({time_ms}ms)"
            return f"✅ **{tool_name} completed**{execution_time}"

    elif update_obj.type == "progress":
        # Handle progress updates
        progress_text = f"🔄 **{update_obj.content or 'Working...'}**"

        percentage = update_obj.get_progress_percentage()
        if percentage is not None:
            # Create a simple progress bar
            filled = int(percentage / 10)  # 0-10 scale
            bar = "█" * filled + "░" * (10 - filled)
            progress_text += f"\n\n`{bar}` {percentage}%"

        if update_obj.progress:
            step = update_obj.progress.get("step")
            total_steps = update_obj.progress.get("total_steps")
            if step and total_steps:
                progress_text += f"\n\nStep {step} of {total_steps}"

        return progress_text

    elif update_obj.type == "error":
        # Handle error messages
        return f"❌ **Error**\n\n_{update_obj.get_error_message()}_"

    elif update_obj.type == "assistant" and update_obj.tool_calls:
        # Show when tools are being called
        tool_names = update_obj.get_tool_names()
        if tool_names:
            tools_text = ", ".join(tool_names)
            return f"🔧 **Using tools:** {tools_text}"

    elif update_obj.type == "assistant" and update_obj.content:
        # Regular content updates with preview
        content_preview = (
            update_obj.content[:150] + "..."
            if len(update_obj.content) > 150
            else update_obj.content
        )
        return f"🤖 **Claude is working...**\n\n_{content_preview}_"

    elif update_obj.type == "system":
        # System initialization or other system messages
        if update_obj.metadata and update_obj.metadata.get("subtype") == "init":
            tools_count = len(update_obj.metadata.get("tools", []))
            model = update_obj.metadata.get("model", "Claude")
            return f"🚀 **Starting {model}** with {tools_count} tools available"

    return None


def _format_error_message(error_str: str) -> str:
    """Format error messages for user-friendly display."""
    if "usage limit reached" in error_str.lower():
        # Usage limit error - already user-friendly from integration.py
        return error_str
    elif "tool not allowed" in error_str.lower():
        # Tool validation error - already handled in facade.py
        return error_str
    elif "no conversation found" in error_str.lower():
        return (
            f"🔄 **Session Not Found**\n\n"
            f"The Claude session could not be found or has expired.\n\n"
            f"**What you can do:**\n"
            f"• Use `/new` to start a fresh session\n"
            f"• Try your request again\n"
            f"• Use `/status` to check your current session"
        )
    elif "rate limit" in error_str.lower():
        return (
            f"⏱️ **Rate Limit Reached**\n\n"
            f"Too many requests in a short time period.\n\n"
            f"**What you can do:**\n"
            f"• Wait a moment before trying again\n"
            f"• Use simpler requests\n"
            f"• Check your current usage with `/status`"
        )
    elif "timeout" in error_str.lower():
        return (
            f"⏰ **Request Timeout**\n\n"
            f"Your request took too long to process and timed out.\n\n"
            f"**What you can do:**\n"
            f"• Try breaking down your request into smaller parts\n"
            f"• Use simpler commands\n"
            f"• Try again in a moment"
        )
    else:
        # Generic error handling
        return (
            f"❌ **Claude Code Error**\n\n"
            f"Failed to process your request: {error_str}\n\n"
            f"Please try again or contact the administrator if the problem persists."
        )


async def handle_text_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle regular text messages as Claude prompts."""
    user_id = update.effective_user.id
    message_text = update.message.text
    settings: Settings = context.bot_data["settings"]

    # Get services
    rate_limiter: Optional[RateLimiter] = context.bot_data.get("rate_limiter")

    logger.info(
        "Processing text message", user_id=user_id, message_length=len(message_text)
    )

    try:
        # Check rate limit
        if rate_limiter:
            allowed, limit_message = await rate_limiter.check_rate_limit(
                user_id
            )
            if not allowed:
                await update.message.reply_text(f"⏱️ {limit_message}")
                return

        # Send typing indicator
        await update.message.chat.send_action("typing")

        # Create progress message
        progress_msg = await update.message.reply_text(
            "🤔 Processing your request...",
            reply_to_message_id=update.message.message_id,
        )

        # Record this prompt to prevent echo via webhook
        webhook_handler = context.bot_data.get("webhook_handler")
        if webhook_handler:
            webhook_handler.record_telegram_prompt(user_id, message_text)

        # Get Claude integration and storage from context
        claude_integration = context.bot_data.get("claude_integration")
        storage = context.bot_data.get("storage")

        if not claude_integration:
            await update.message.reply_text(
                "❌ **Claude integration not available**\n\n"
                "The Claude Code integration is not properly configured. "
                "Please contact the administrator.",
                parse_mode="Markdown",
            )
            return


        # Enhanced stream updates handler with progress tracking
        async def stream_handler(update_obj):
            try:
                progress_text = await _format_progress_update(update_obj)
                if progress_text:
                    try:
                        await progress_msg.edit_text(progress_text, parse_mode="Markdown")
                    except Exception as edit_error:
                        logger.warning("Failed to edit progress message", error=str(edit_error))
            except Exception as e:
                logger.warning("Failed to update progress message", error=str(e))

        # Run Claude command
        try:
            claude_response = await claude_integration.run_command(
                prompt=message_text,
                user_id=user_id,
                on_stream=stream_handler,
            )

            # Log interaction to storage
            if storage:
                try:
                    await storage.save_claude_interaction(
                        user_id=user_id,
                        session_id=f"user_{user_id}",
                        prompt=message_text,
                        response=claude_response,
                        ip_address=None,  # Telegram doesn't provide IP
                    )
                except Exception as e:
                    logger.warning("Failed to log interaction to storage", error=str(e))

            # Hook monitoring handles all response formatting and sending
            logger.info("Claude command sent - response will be delivered via hook monitoring", user_id=user_id)
            
            # Delete progress message (hook will send the actual response)
            try:
                await progress_msg.delete()
            except Exception as delete_error:
                logger.warning("Failed to delete progress message", error=str(delete_error))
            
            
            logger.info("Text message processed successfully - hook monitoring will deliver response", user_id=user_id)
            return

        except ClaudeToolValidationError as e:
            # Tool validation error with detailed instructions
            logger.error(
                "Tool validation error",
                error=str(e),
                user_id=user_id,
                blocked_tools=e.blocked_tools,
            )
            
            # Delete progress message and send error
            try:
                await progress_msg.delete()
            except Exception as delete_error:
                logger.warning("Failed to delete progress message", error=str(delete_error))
            await _safe_reply_text(
                update,
                str(e),
                parse_mode="Markdown",
                reply_to_message_id=update.message.message_id,
            )
            
            
        except Exception as e:
            logger.error("Claude integration failed", error=str(e), user_id=user_id)
            
            # Delete progress message and send error
            try:
                await progress_msg.delete()
            except Exception as delete_error:
                logger.warning("Failed to delete progress message", error=str(delete_error))
            error_message = _format_error_message(str(e))
            await _safe_reply_text(
                update,
                error_message,
                parse_mode="Markdown",
                reply_to_message_id=update.message.message_id,
            )
            

    except Exception as e:
        # Clean up progress message if it exists
        try:
            await progress_msg.delete()
        except Exception as delete_error:
            logger.warning("Failed to delete progress message in exception handler", error=str(delete_error))

        error_msg = f"❌ **Error processing message**\n\n{str(e)}"
        await _safe_reply_text(update, error_msg, parse_mode="Markdown")


        logger.error("Error processing text message", error=str(e), user_id=user_id)










