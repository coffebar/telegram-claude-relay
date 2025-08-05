"""Command handlers for bot operations."""

import structlog

from telegram import Update
from telegram.ext import ContextTypes


ENABLE_BUTTONS = False


logger = structlog.get_logger()


def create_keyboard(*args, **kwargs):
    """Create keyboard only if buttons are enabled."""
    if not ENABLE_BUTTONS:
        return None
    return create_keyboard(*args, **kwargs)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    user = update.effective_user

    welcome_message = (
        f"👋 Hello {user.first_name}!\n\n"
        f"🤖 This bot connects you directly to Claude Code via tmux.\n\n"
        f"**How to use:**\n"
        f"Just send any message and it will go directly to Claude."
    )

    reply_markup = create_keyboard([])

    await update.message.reply_text(
        welcome_message, parse_mode="Markdown", reply_markup=reply_markup
    )

    # Log command
    logger.info("Start command executed", user_id=user.id)


async def _forward_claude_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE, command: str
) -> None:
    """Generic handler to forward commands to Claude."""
    user = update.effective_user

    # Get Claude integration from context
    claude_integration = context.bot_data.get("claude_integration")
    if not claude_integration:
        await update.message.reply_text("❌ Claude service is not available.")
        return

    try:
        # Send typing indicator
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id, action="typing"
        )

        # Send command directly to Claude via tmux
        await claude_integration._ensure_tmux_integration()
        await claude_integration.tmux_integration.tmux_client.send_command(command)

    except Exception as e:
        logger.error(
            f"Error executing {command} command", error=str(e), user_id=user.id
        )
        await update.message.reply_text(
            f"❌ Failed to execute {command}. Please try again."
        )

    # Log command
    logger.info(f"{command} command executed", user_id=user.id)


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /clear command - forwards to Claude."""
    await _forward_claude_command(update, context, "/clear")


async def compact_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /compact command - forwards to Claude."""
    await _forward_claude_command(update, context, "/compact")


async def self_update_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /self_update command - updates bot from GitHub and restarts."""
    user = update.effective_user

    # Send initial message
    await update.message.reply_text(
        "🔄 Self-update initiated...\n"
        "The bot will:\n"
        "1. Pull latest changes from GitHub\n"
        "2. Restart automatically\n"
        "3. Be back online in a few seconds"
    )

    logger.info("Self-update command executed", user_id=user.id)

    # Exit with code 42 to trigger update in wrapper script
    import sys

    sys.exit(42)
