"""Command handlers for bot operations."""

import structlog
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update

ENABLE_BUTTONS = False

from telegram.ext import ContextTypes

from ...claude.facade import ClaudeIntegration
from ...config.settings import Settings
from ...security.audit import AuditLogger
from ...security.validators import SecurityValidator

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
        f"Just send any message and it will go directly to Claude.\n\n"
        f"That's it! No commands needed."
    )

    reply_markup = create_keyboard([])

    await update.message.reply_text(
        welcome_message, parse_mode="Markdown", reply_markup=reply_markup
    )

    # Log command
    audit_logger: AuditLogger = context.bot_data.get("audit_logger")
    if audit_logger:
        await audit_logger.log_command(
            user_id=user.id, command="start", args=[], success=True
        )

