"""Robust message sending utilities with fallback support."""

from typing import Any, Optional, Union

import structlog
from telegram import Bot, InlineKeyboardMarkup, Message
from telegram.constants import ParseMode
from telegram.error import BadRequest

logger = structlog.get_logger()


class RobustMessageSender:
    """Handles message sending with automatic fallback when markdown parsing fails."""

    def __init__(self, bot: Bot):
        """Initialize the message sender."""
        self.bot = bot

    async def send_message(
        self,
        chat_id: Union[int, str],
        text: str,
        parse_mode: Optional[str] = ParseMode.MARKDOWN_V2,
        reply_markup: Optional[InlineKeyboardMarkup] = None,
        disable_web_page_preview: bool = True,
        **kwargs: Any,
    ) -> Optional[Message]:
        """
        Send a message with automatic fallback to plain text if formatting fails.

        Args:
            chat_id: Target chat ID
            text: Message text
            parse_mode: Initial parse mode to try (defaults to Markdown)
            reply_markup: Optional inline keyboard
            disable_web_page_preview: Whether to disable link previews
            **kwargs: Additional arguments to pass to send_message

        Returns:
            Sent message object or None if all attempts failed
        """
        # First attempt: Try with requested parse mode
        if parse_mode:
            try:
                return await self.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup,
                    disable_web_page_preview=disable_web_page_preview,
                    **kwargs,
                )
            except BadRequest as e:
                error_str = str(e)
                if "can't parse entities" in error_str.lower():
                    logger.warning(
                        "Markdown parsing failed, attempting fallback",
                        error=error_str,
                        parse_mode=parse_mode,
                        text_length=len(text),
                        text_preview=text[:100] + "..." if len(text) > 100 else text,
                    )
                else:
                    # Not a parsing error, re-raise
                    raise

        # Second attempt: Try HTML if MarkdownV2 failed
        if parse_mode == ParseMode.MARKDOWN_V2 or parse_mode == ParseMode.MARKDOWN:
            try:
                # Convert basic markdown to HTML
                html_text = self._convert_markdown_to_html(text)
                return await self.bot.send_message(
                    chat_id=chat_id,
                    text=html_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup,
                    disable_web_page_preview=disable_web_page_preview,
                    **kwargs,
                )
            except BadRequest as e:
                logger.warning(
                    "HTML parsing also failed",
                    error=str(e),
                    text_length=len(text),
                )

        # Final attempt: Send as plain text without any formatting
        try:
            logger.info(
                "Falling back to plain text",
                chat_id=chat_id,
                text_length=len(text),
            )
            return await self.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
                disable_web_page_preview=disable_web_page_preview,
                **kwargs,
            )
        except Exception as e:
            logger.error(
                "Failed to send message even as plain text",
                error=str(e),
                chat_id=chat_id,
            )
            return None

    async def edit_message_text(
        self,
        chat_id: Union[int, str],
        message_id: int,
        text: str,
        parse_mode: Optional[str] = ParseMode.MARKDOWN_V2,
        reply_markup: Optional[InlineKeyboardMarkup] = None,
        **kwargs: Any,
    ) -> Optional[Message]:
        """
        Edit a message with automatic fallback to plain text if formatting fails.

        Args:
            chat_id: Target chat ID
            message_id: Message ID to edit
            text: New message text
            parse_mode: Initial parse mode to try (defaults to Markdown)
            reply_markup: Optional inline keyboard
            **kwargs: Additional arguments to pass to edit_message_text

        Returns:
            Edited message object or None if all attempts failed
        """
        # First attempt: Try with requested parse mode
        if parse_mode:
            try:
                return await self.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=text,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup,
                    **kwargs,
                )
            except BadRequest as e:
                error_str = str(e)
                if "can't parse entities" in error_str.lower():
                    logger.warning(
                        "Markdown parsing failed on edit, attempting fallback",
                        error=error_str,
                        parse_mode=parse_mode,
                    )
                else:
                    # Not a parsing error, re-raise
                    raise

        # Second attempt: Try HTML if MarkdownV2 failed
        if parse_mode == ParseMode.MARKDOWN_V2 or parse_mode == ParseMode.MARKDOWN:
            try:
                html_text = self._convert_markdown_to_html(text)
                return await self.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=html_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup,
                    **kwargs,
                )
            except BadRequest as e:
                logger.warning(
                    "HTML parsing also failed on edit",
                    error=str(e),
                )

        # Final attempt: Edit as plain text
        try:
            logger.info(
                "Falling back to plain text for edit",
                chat_id=chat_id,
                message_id=message_id,
            )
            return await self.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=reply_markup,
                **kwargs,
            )
        except Exception as e:
            logger.error(
                "Failed to edit message even as plain text",
                error=str(e),
                chat_id=chat_id,
                message_id=message_id,
            )
            return None

    def _convert_markdown_to_html(self, text: str) -> str:
        """
        Convert basic Markdown formatting to HTML.

        This is a simple conversion that handles common cases.
        """
        import html

        # First escape HTML entities
        text = html.escape(text)

        # Convert markdown code blocks to HTML
        # Handle multi-line code blocks first
        import re

        # Multi-line code blocks with language hint
        text = re.sub(
            r"```(\w+)?\n(.*?)\n```",
            r"<pre><code>\2</code></pre>",
            text,
            flags=re.DOTALL,
        )

        # Inline code
        text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)

        # Bold text
        text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)

        # Italic text
        text = re.sub(r"\*([^*]+)\*", r"<i>\1</i>", text)

        # Underline text
        text = re.sub(r"__([^_]+)__", r"<u>\1</u>", text)

        # Strike-through text
        text = re.sub(r"~~([^~]+)~~", r"<s>\1</s>", text)

        return text
