"""Webhook handler for receiving Claude hook events and providing live status updates."""

from typing import Any, Dict, Optional

import structlog

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from ...config.settings import Settings


logger = structlog.get_logger()


class MessageTracker:
    """Tracks the last status messages for live-updating."""

    def __init__(self):
        self.last_status_messages: Dict[int, Dict[str, Any]] = (
            {}
        )  # user_id -> {message_id, chat_id, type, content}
        self.pending_tool_operations: Dict[str, Dict[str, Any]] = (
            {}
        )  # "session_id:tool_name" -> {user_id, message_id, chat_id, content, timestamp, tool_name, message_series}

    def get_message_type(
        self, message: str, original_message: Dict[str, Any] = None
    ) -> str:
        """Determine the type of message based on content and context."""
        if "💬 **New Prompt:**" in message:
            return "prompt"
        elif "📝 **Managing todos:**" in message:
            # Check if this is a TodoWrite from a pre/post tool hook
            if original_message and original_message.get("tool_name") == "TodoWrite":
                notification_type = original_message.get("notification_type")
                if notification_type == "pre_tool_use":
                    return "pre_tool"
                elif notification_type == "post_tool_use":
                    return "post_tool"
            # Otherwise it's a regular todo list message
            return "todo_list"  # Special type for TodoWrite - always send new message
        elif any(prefix in message for prefix in ["✏️", "📝", "👁️", "💻", "🔍", "🔧"]):
            return "pre_tool"
        elif "✅" in message:
            return "post_tool"
        elif "🤖 **Claude:**" in message:
            return "response"
        else:
            return "other"

    def should_edit_last_message(
        self, user_id: int, message_type: str
    ) -> tuple[bool, Optional[Dict[str, Any]]]:
        """Check if we should edit the last message instead of sending a new one."""
        last_msg = self.last_status_messages.get(user_id)

        logger.debug(
            "Checking message edit",
            user_id=user_id,
            message_type=message_type,
            has_last_msg=bool(last_msg),
            last_type=last_msg.get("type") if last_msg else None,
        )

        if not last_msg:
            logger.debug("No last message found, will send new")
            return False, None

        last_type = last_msg.get("type")

        # Clear status tracking for new prompts, responses, or todo lists (start fresh)
        if message_type in ["prompt", "response", "todo_list"]:
            logger.debug("Clearing status tracking for new prompt/response/todo_list")
            self.last_status_messages.pop(user_id, None)
            return False, None

        # For tool-related messages, we now use signature-based matching instead of timing
        # This handles cases where tools take minutes to complete
        if message_type in ["pre_tool", "post_tool"]:
            logger.debug("Tool message detected, using signature-based matching")
            return False, None  # Will be handled by signature-based logic

        logger.debug(
            "Will send new message",
            reason=f"type mismatch: {last_type} -> {message_type}",
        )
        return False, None

    def create_tool_signature(self, tool_name: str, tool_params: Dict[str, Any]) -> str:
        """Create a unique signature for a tool operation based on its parameters."""
        import hashlib
        import json

        # Create a consistent string representation of the tool and its parameters
        # NOTE: Do NOT include timestamp - pre and post hooks need the same signature
        signature_data = {"tool": tool_name, "params": tool_params}

        # Convert to JSON string and hash it
        signature_str = json.dumps(signature_data, sort_keys=True)
        return hashlib.md5(signature_str.encode()).hexdigest()[
            :12
        ]  # Longer hash for better uniqueness

    def register_tool_operation(
        self,
        session_id: str,
        user_id: int,
        message_id: int,
        chat_id: int,
        content: str,
        tool_name: str,
    ) -> None:
        """Register a pre_tool operation for later matching with post_tool."""
        import time

        # Create composite key: session_id:tool_name for precise matching
        operation_key = f"{session_id}:{tool_name}"

        self.pending_tool_operations[operation_key] = {
            "user_id": user_id,
            "message_id": message_id,
            "chat_id": chat_id,
            "content": content,
            "tool_name": tool_name,
            "timestamp": time.time(),
        }

        logger.info(
            "Registered tool operation",
            operation_key=operation_key,
            session_id=session_id,
            tool_name=tool_name,
            user_id=user_id,
            message_id=message_id,
        )

    def find_matching_tool_operation(
        self, session_id: str, tool_name: str
    ) -> Optional[Dict[str, Any]]:
        """Find the matching pre_tool operation for a post_tool (without removing it yet)."""
        # Create composite key: session_id:tool_name for precise matching
        operation_key = f"{session_id}:{tool_name}"
        operation = self.pending_tool_operations.get(operation_key)

        if operation:
            logger.info(
                "Found matching tool operation",
                operation_key=operation_key,
                session_id=session_id,
                tool_name=tool_name,
                user_id=operation["user_id"],
                message_id=operation["message_id"],
            )
        else:
            logger.warning(
                "No matching pre_tool operation found",
                operation_key=operation_key,
                session_id=session_id,
                tool_name=tool_name,
                pending_count=len(self.pending_tool_operations),
                pending_operations=list(self.pending_tool_operations.keys()),
            )

        return operation

    def remove_tool_operation(self, session_id: str, tool_name: str) -> None:
        """Remove a tool operation after successful processing."""
        operation_key = f"{session_id}:{tool_name}"
        removed_operation = self.pending_tool_operations.pop(operation_key, None)
        if removed_operation:
            logger.info(
                "Removed processed tool operation",
                operation_key=operation_key,
                session_id=session_id,
                tool_name=tool_name,
            )

    def cleanup_old_operations(self, max_age_seconds: int = 600) -> None:
        """Clean up tool operations older than max_age_seconds (default 10 minutes)."""
        import time

        current_time = time.time()

        expired_operations = [
            operation_key
            for operation_key, op in self.pending_tool_operations.items()
            if current_time - op["timestamp"] > max_age_seconds
        ]

        for operation_key in expired_operations:
            del self.pending_tool_operations[operation_key]
            logger.info(
                "Cleaned up expired tool operation", operation_key=operation_key
            )

    def track_message(
        self,
        user_id: int,
        message_id: int,
        chat_id: int,
        message_type: str,
        content: str = "",
    ) -> None:
        """Track a message for potential editing."""
        if message_type in ["pre_tool", "post_tool"]:
            self.last_status_messages[user_id] = {
                "message_id": message_id,
                "chat_id": chat_id,
                "type": message_type,
                "content": content,
            }


class ConversationWebhookHandler:
    """Handles incoming webhook requests with Claude conversation updates."""

    def __init__(self, bot: Bot, settings: Settings):
        self.bot = bot
        self.settings = settings
        self.session_to_chat: Dict[str, int] = {}  # session_id -> chat_id mapping
        self.subscribed_users: set[int] = set()  # Track subscribed users
        self.last_telegram_prompts: Dict[int, str] = (
            {}
        )  # user_id -> last prompt sent via Telegram
        self.message_tracker = MessageTracker()
        self.permission_dialogs: Dict[str, Dict[str, Any]] = (
            {}
        )  # dialog_id -> dialog info

    async def handle_conversation_update(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Process incoming conversation update from Claude hook."""
        logger.info("Webhook handler called", data_keys=list(data.keys()))
        try:
            session_id = data.get("session_id")
            message = data.get("message", {})

            logger.info(
                "Processing conversation update",
                session_id=session_id,
                message_type=message.get("type"),
                role=message.get("role"),
            )

            if not session_id or not message:
                logger.warning(
                    "Missing required fields",
                    session_id=session_id,
                    has_message=bool(message),
                )
                return {"status": "error", "message": "Missing required fields"}

            # Check if this is a permission dialog
            if message.get("type") == "permission_dialog":
                logger.info(
                    "Handling permission dialog",
                    session_id=session_id,
                    question=message.get("content", "")[:50],
                )
                await self._send_permission_dialog(session_id, message)
            else:
                # Format the message for Telegram
                formatted_message = self._format_message(message)

                if formatted_message:
                    logger.info(
                        "Sending formatted message to Telegram",
                        session_id=session_id,
                        msg_len=len(formatted_message),
                    )
                    # Send to all active chats for this session
                    await self._send_to_telegram(session_id, formatted_message, message)
                else:
                    logger.info(
                        "Message filtered out during formatting",
                        message_type=message.get("type"),
                        role=message.get("role"),
                    )

            return {"status": "ok"}

        except Exception as e:
            logger.error("Error handling conversation update", error=str(e))
            return {"status": "error", "message": str(e)}

    def _format_message(self, message: Dict[str, Any]) -> Optional[str]:
        """Format Claude message for Telegram display."""
        msg_type = message.get("type")
        role = message.get("role")
        content = message.get("content", "")

        if not content:
            return None

        # Format based on message type
        if msg_type == "thinking":
            # Show thinking steps in italic
            return f"💭 _Thinking: {content}_"

        elif msg_type == "message" and role == "assistant":
            # Regular Claude response - will be handled by message splitting in _send_new_message
            return f"🤖 **Claude:**\n{content}"

        elif msg_type == "message" and role == "user":
            # Echo user messages (optional)
            return None  # Skip user messages as they're already in Telegram

        elif msg_type == "permission_dialog" and role == "system":
            # Permission dialog - will be handled specially with inline keyboard
            return None  # Handled separately in _send_to_telegram

        elif msg_type == "hook_notification" and role == "system":
            # Check if this is a "New Prompt" notification that matches a recent Telegram prompt
            if "💬 **New Prompt:**" in content:
                # Extract the prompt content from the formatted message
                prompt_start = content.find("```\n") + 4
                prompt_end = content.rfind("\n```")
                if prompt_start > 3 and prompt_end > prompt_start:
                    hook_prompt = content[prompt_start:prompt_end]

                    # Check if this matches any recent Telegram prompt
                    for user_id, last_prompt in self.last_telegram_prompts.items():
                        if hook_prompt.strip() == last_prompt.strip():
                            logger.info(
                                "Skipping echo of Telegram prompt",
                                user_id=user_id,
                                prompt_length=len(hook_prompt),
                            )
                            return None  # Skip this notification

            # Real-time hook notifications
            return content  # Already formatted by ConversationMonitor

        # Tool calls
        tool_calls = message.get("tool_calls", [])
        if tool_calls:
            tools_text = self._format_tool_calls(tool_calls)
            if tools_text:
                return f"🔧 **Tools Used:**\n{tools_text}"

        return None

    def _format_tool_calls(self, tool_calls: list) -> str:
        """Format tool calls for display."""
        formatted = []
        for tool in tool_calls:
            name = tool.get("name", "Unknown")
            params = tool.get("parameters", {})

            # Format based on tool type
            if name == "Edit":
                file_path = params.get("file_path", "")
                formatted.append(f"✏️ Edited: `{file_path}`")
            elif name == "Write":
                file_path = params.get("file_path", "")
                formatted.append(f"📝 Created: `{file_path}`")
            elif name == "Read":
                file_path = params.get("file_path", "")
                formatted.append(f"👁️ Read: `{file_path}`")
            elif name == "Bash":
                command = params.get("command", "")
                if len(command) > 50:
                    command = command[:50] + "..."
                formatted.append(f"💻 Ran: `{command}`")
            else:
                formatted.append(f"🔧 {name}")

        return "\n".join(formatted)

    async def _send_to_telegram(
        self, session_id: str, message: str, original_message: Dict[str, Any] = None
    ) -> None:
        """Send message to all relevant Telegram chats, with signature-based tool matching."""
        # Send to all subscribed users
        # If no users are subscribed yet, fall back to allowed users
        users_to_notify = (
            self.subscribed_users
            if self.subscribed_users
            else (self.settings.allowed_users or [])
        )

        # Determine message type with context from original message
        message_type = self.message_tracker.get_message_type(message, original_message)

        logger.debug(
            "Processing webhook message",
            message_type=message_type,
            user_count=len(users_to_notify),
            message_preview=message[:50],
            has_original_message=bool(original_message),
            original_message_keys=(
                list(original_message.keys()) if original_message else []
            ),
        )

        for user_id in users_to_notify:
            try:
                await self._handle_message_for_user(
                    user_id, message, message_type, original_message, session_id
                )
            except Exception as e:
                logger.warning(
                    "Failed to send update to user", user_id=user_id, error=str(e)
                )

    async def _handle_message_for_user(
        self,
        user_id: int,
        message: str,
        message_type: str,
        original_message: Dict[str, Any] = None,
        session_id: str = "",
    ) -> None:
        """Handle message for a specific user with signature-based tool matching."""

        # Check if this is a tool-related message
        if message_type in ["pre_tool", "post_tool"] and original_message:
            tool_name = original_message.get("tool_name")
            tool_params = original_message.get("tool_params", {})

            logger.info(
                "Processing tool message",
                message_type=message_type,
                tool_name=tool_name,
                has_tool_params=bool(tool_params),
                tool_params_keys=list(tool_params.keys()) if tool_params else [],
                original_message_keys=(
                    list(original_message.keys()) if original_message else []
                ),
            )

            if tool_name and session_id:
                logger.info(
                    "Processing tool message with session-based matching",
                    message_type=message_type,
                    tool_name=tool_name,
                    session_id=session_id,
                    has_tool_params=bool(tool_params),
                )

                if message_type == "pre_tool":
                    # Register the operation IMMEDIATELY to prevent race conditions
                    # (we'll update with the actual message_id after sending)
                    temp_operation_key = f"{session_id}:{tool_name}"
                    self.message_tracker.pending_tool_operations[temp_operation_key] = {
                        "user_id": user_id,
                        "message_id": 0,  # Temporary, will be updated
                        "chat_id": user_id,
                        "content": message,
                        "tool_name": tool_name,
                        "timestamp": __import__("time").time(),
                    }

                    logger.info(
                        "Pre-registered tool operation (immediate)",
                        operation_key=temp_operation_key,
                        session_id=session_id,
                        tool_name=tool_name,
                        user_id=user_id,
                    )

                    # Send new message (potentially as series)
                    try:
                        # Debug: Log the exact message content being sent to Telegram
                        logger.info(
                            "About to send pre_tool message to Telegram",
                            operation_key=temp_operation_key,
                            message_length=len(message),
                            message_content=message[
                                :500
                            ],  # First 500 chars to avoid log spam
                            message_preview=repr(
                                message[:100]
                            ),  # Show special characters
                        )

                        # Send message series if needed
                        result = await self._send_message_series(user_id, message)

                        # Update the operation with the actual message_id IMMEDIATELY after sending
                        # This prevents race conditions where post-tool arrives before update
                        if (
                            temp_operation_key
                            in self.message_tracker.pending_tool_operations
                        ):
                            self.message_tracker.pending_tool_operations[
                                temp_operation_key
                            ]["message_id"] = result["last_message_id"]
                            # Store the last message content for consistent editing
                            self.message_tracker.pending_tool_operations[
                                temp_operation_key
                            ]["content"] = result["last_content"]
                            # Store the message series info
                            self.message_tracker.pending_tool_operations[
                                temp_operation_key
                            ]["message_series"] = result["message_series"]

                            logger.info(
                                "Updated tool operation with message_id",
                                operation_key=temp_operation_key,
                                message_id=result["last_message_id"],
                                total_parts=result["total_parts"],
                                sent_parts=result["sent_parts"],
                            )
                        else:
                            logger.warning(
                                "Tool operation was removed by post-tool before message_id update",
                                operation_key=temp_operation_key,
                                message_id=result.get("last_message_id", "unknown"),
                            )
                    except Exception as send_error:
                        # If message sending fails, remove the registered operation
                        self.message_tracker.pending_tool_operations.pop(
                            temp_operation_key, None
                        )

                        logger.warning(
                            "Failed to send pre_tool message, removed registered operation",
                            operation_key=temp_operation_key,
                            error=str(send_error),
                        )

                        # Don't re-raise the error - instead return early to avoid further processing
                        return

                elif message_type == "post_tool":
                    # Find matching pre_tool operation
                    matching_operation = (
                        self.message_tracker.find_matching_tool_operation(
                            session_id, tool_name
                        )
                    )

                    # Wait for valid message_id if operation was just created
                    if matching_operation:
                        # If message_id is 0, the pre-tool message might still be sending
                        # Give it a moment to complete
                        if matching_operation.get("message_id", 0) == 0:
                            import asyncio

                            await asyncio.sleep(0.1)  # Brief wait for message_id update
                            # Re-fetch the operation to get updated message_id
                            matching_operation = (
                                self.message_tracker.find_matching_tool_operation(
                                    session_id, tool_name
                                )
                            )

                        if (
                            matching_operation
                            and matching_operation.get("message_id", 0) > 0
                        ):
                            # Edit the existing pre_tool message to append completion status
                            try:
                                pre_tool_content = matching_operation["content"]
                                combined_message = f"{pre_tool_content}\n\n{message}"

                                # Sanitize message for Telegram Markdown parsing
                                sanitized_message = self._sanitize_markdown(
                                    combined_message
                                )

                                await self.bot.edit_message_text(
                                    chat_id=matching_operation["chat_id"],
                                    message_id=matching_operation["message_id"],
                                    text=sanitized_message,
                                    parse_mode=ParseMode.MARKDOWN,
                                )

                                logger.info(
                                    "Successfully combined pre/post tool messages",
                                    session_id=session_id,
                                    tool_name=tool_name,
                                    user_id=user_id,
                                    message_id=matching_operation["message_id"],
                                )

                                # Remove the operation after successful processing
                                self.message_tracker.remove_tool_operation(
                                    session_id, tool_name
                                )

                            except Exception as edit_error:
                                logger.warning(
                                    "Failed to edit pre_tool message, sending new post_tool message",
                                    session_id=session_id,
                                    tool_name=tool_name,
                                    message_id=matching_operation.get("message_id"),
                                    error=str(edit_error),
                                )
                                # Fallback: send as new message
                                await self._send_new_message(
                                    user_id, message, message_type
                                )
                    else:
                        # No matching pre_tool found, send as new message
                        logger.warning(
                            "No matching pre_tool operation found for post_tool",
                            session_id=session_id,
                            tool_name=tool_name,
                        )
                        await self._send_new_message(user_id, message, message_type)

                # Clean up old operations periodically
                self.message_tracker.cleanup_old_operations()
                return

        # For non-tool messages or tool messages without proper signature data
        await self._send_new_message(user_id, message, message_type)

    def _sanitize_markdown(self, text: str) -> str:
        """Sanitize text to prevent Telegram Markdown parsing errors while preserving formatting."""
        import re

        # Only apply minimal sanitization to prevent parsing errors, not full escaping
        # This preserves intended Markdown formatting while fixing edge cases
        # Fix unmatched backticks that break parsing
        # Count backticks and ensure they're properly paired
        backtick_count = text.count("`")
        if backtick_count % 2 == 1:
            # Odd number of backticks - escape the last one to prevent parsing errors
            last_backtick_pos = text.rfind("`")
            if last_backtick_pos != -1:
                text = text[:last_backtick_pos] + "\\`" + text[last_backtick_pos + 1 :]

        # Fix unmatched code block markers
        code_block_starts = text.count("```")
        if code_block_starts % 2 == 1:
            # Odd number of code block markers - add closing marker
            if not text.endswith("\n"):
                text += "\n"
            text += "```"

        # Only escape characters that commonly break Telegram parsing in specific contexts
        # Don't escape formatting characters like *, _, #, etc. as they're intentional

        # Fix problematic character sequences that break entity parsing
        # These are edge cases found in logs, not general Markdown escaping
        text = re.sub(
            r"([^\\])(\[)([^\]]*?)(\])\(([^)]*?)\)", r"\1\\\2\3\\\4(\5)", text
        )  # Fix problematic links
        text = re.sub(r"(\w)([<>])(\w)", r"\1\\\2\3", text)  # Escape < > between words

        return text

    def _split_long_message(self, text: str, max_length: int = 3900) -> list[str]:
        """Split long messages into multiple parts while preserving formatting and structure."""
        if len(text) <= max_length:
            return [text]

        parts = []
        remaining = text

        while len(remaining) > max_length:
            # Find a good split point (prefer line breaks, then spaces)
            split_point = max_length

            # Try to split at line break
            last_newline = remaining.rfind("\n", 0, max_length)
            if last_newline > max_length * 0.7:  # Don't split too early
                split_point = last_newline + 1
            else:
                # Try to split at space
                last_space = remaining.rfind(" ", 0, max_length)
                if last_space > max_length * 0.8:  # Don't split too early
                    split_point = last_space + 1

            # Extract the part
            part = remaining[:split_point]

            # Handle code blocks - ensure they're properly closed/opened
            if "```" in part:
                open_blocks = part.count("```") % 2
                if open_blocks == 1:
                    # Close the code block in this part
                    part += "\n```"
                    # Open it in the next part
                    remaining = "```\n" + remaining[split_point:]
                else:
                    remaining = remaining[split_point:]
            else:
                remaining = remaining[split_point:]

            parts.append(part.rstrip())

        # Add the final part
        if remaining.strip():
            parts.append(remaining.strip())

        return parts

    async def _send_message_series(
        self, user_id: int, message: str, parse_mode=ParseMode.MARKDOWN
    ) -> dict:
        """Send a message as a series if it's too long, return info about the last message."""
        # Sanitize message for Telegram Markdown parsing
        sanitized_message = self._sanitize_markdown(message)

        # Split message if needed
        message_parts = self._split_long_message(sanitized_message)

        sent_messages = []
        for i, part in enumerate(message_parts):
            try:
                sent_msg = await self.bot.send_message(
                    chat_id=user_id, text=part, parse_mode=parse_mode
                )
                sent_messages.append(
                    {
                        "message_id": sent_msg.message_id,
                        "content": part,
                        "part_number": i + 1,
                        "total_parts": len(message_parts),
                    }
                )

                # Small delay between messages to avoid rate limiting
                if i < len(message_parts) - 1:
                    import asyncio

                    await asyncio.sleep(0.1)

            except Exception as e:
                logger.warning(
                    f"Failed to send message part {i+1}/{len(message_parts)}",
                    error=str(e),
                )
                # If sending fails, return info about the last successful message
                break

        if not sent_messages:
            raise Exception("Failed to send any message parts")

        # Return info about the series, focusing on the last message for editing
        last_message = sent_messages[-1]
        return {
            "last_message_id": last_message["message_id"],
            "last_content": last_message["content"],
            "message_series": sent_messages,
            "total_parts": len(message_parts),
            "sent_parts": len(sent_messages),
        }

    async def _send_new_message(
        self, user_id: int, message: str, message_type: str
    ) -> None:
        """Send a new message and track it."""
        try:
            result = await self._send_message_series(user_id, message)

            # Store the message info for potential editing (use last message for editing)
            self.message_tracker.track_message(
                user_id,
                result["last_message_id"],
                user_id,
                message_type,
                result["last_content"],
            )
        except Exception as e:
            logger.error(f"Failed to send new message: {str(e)}")

    async def _send_permission_dialog(
        self, session_id: str, message: Dict[str, Any]
    ) -> None:
        """Send permission dialog with inline keyboard buttons to users."""
        question = message.get("content", "")
        options = message.get("options", [])
        dialog_id = message.get("dialog_id", f"dialog_{session_id}")

        if not question or not options or len(options) != 3:
            logger.warning(
                "Invalid permission dialog",
                question_length=len(question),
                options_count=len(options),
            )
            return

        # Store dialog info for callback handling
        self.permission_dialogs[dialog_id] = {
            "session_id": session_id,
            "question": question,
            "options": options,
            "timestamp": message.get("timestamp"),
        }

        # Create inline keyboard with the 3 options
        keyboard = [
            [
                InlineKeyboardButton(
                    f"✅ {options[0]}", callback_data=f"perm_{dialog_id}_1"
                )
            ],
            [
                InlineKeyboardButton(
                    f"🔄 {options[1]}", callback_data=f"perm_{dialog_id}_2"
                )
            ],
            [
                InlineKeyboardButton(
                    f"❌ {options[2]}", callback_data=f"perm_{dialog_id}_3"
                )
            ],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Format the message - question already includes the header
        formatted_message = f"{question}\n\nPlease select an option:"

        # Send to all subscribed users
        users_to_notify = (
            self.subscribed_users
            if self.subscribed_users
            else (self.settings.allowed_users or [])
        )

        for user_id in users_to_notify:
            try:
                await self.bot.send_message(
                    chat_id=user_id,
                    text=formatted_message,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=reply_markup,
                )
                logger.info(
                    "Sent permission dialog to user",
                    user_id=user_id,
                    dialog_id=dialog_id,
                )
            except Exception as e:
                logger.warning(
                    "Failed to send permission dialog to user",
                    user_id=user_id,
                    error=str(e),
                )

    async def handle_permission_callback(self, callback_query, context) -> None:
        """Handle permission dialog button callbacks."""
        try:
            callback_data = callback_query.data
            user_id = callback_query.from_user.id

            logger.info(
                "Received permission callback",
                user_id=user_id,
                callback_data=callback_data,
            )

            # Parse callback data: perm_{dialog_id}_{option_number}
            if not callback_data.startswith("perm_"):
                logger.warning("Invalid permission callback data", data=callback_data)
                return

            # Remove "perm_" prefix and split by last underscore to get option number
            remaining = callback_data[5:]  # Remove "perm_"
            parts = remaining.rsplit("_", 1)  # Split from right, only once
            if len(parts) != 2:
                logger.warning("Malformed permission callback data", data=callback_data)
                return

            dialog_id = parts[0]
            option_number = parts[1]

            # Get dialog info
            dialog_info = self.permission_dialogs.get(dialog_id)
            if not dialog_info:
                await callback_query.answer("This permission dialog has expired.")
                return

            # Send the response to Claude using the same integration as regular messages
            await self._send_permission_response_to_claude(
                callback_query, context, dialog_info, option_number
            )

            # Update the message to show the selected option
            option_text = dialog_info["options"][int(option_number) - 1]
            updated_message = (
                f"{dialog_info['question']}\n\n"
                f"✅ **Selected:** {option_number}. {option_text}"
            )

            await callback_query.edit_message_text(
                text=updated_message,
                parse_mode=ParseMode.MARKDOWN,
            )

            await callback_query.answer(f"Selected option {option_number}")

            # Clean up dialog info
            del self.permission_dialogs[dialog_id]

            logger.info(
                "Permission callback handled successfully",
                user_id=user_id,
                dialog_id=dialog_id,
                option=option_number,
            )

        except Exception as e:
            logger.error("Error handling permission callback", error=str(e))
            try:
                await callback_query.answer("Error processing your selection.")
            except Exception:
                pass

    async def _send_permission_response_to_claude(
        self, callback_query, context, dialog_info: Dict[str, Any], option_number: str
    ) -> None:
        """Send the permission response to Claude using the same path as regular messages."""
        try:
            # Get Claude integration from context (same as regular messages)
            # This is the proper way to access bot_data in callback handlers
            claude_integration = context.bot_data.get("claude_integration")
            if not claude_integration:
                logger.error("Claude integration not available in bot_data")
                # Fallback to direct tmux approach
                await self._send_permission_response_via_tmux(option_number)
                return

            # Send the option number using the same method as regular messages
            # This ensures it goes through the proper Claude integration pipeline
            logger.info(
                "About to send permission response to Claude",
                option=option_number,
                user_id=callback_query.from_user.id,
                has_integration=bool(claude_integration),
            )

            result = await claude_integration.run_command(
                prompt=option_number,
                user_id=callback_query.from_user.id,  # Use the actual user ID who clicked the button
                on_stream=None,  # No stream handling needed for simple responses
            )

            logger.info(
                "Sent permission response to Claude via integration",
                option=option_number,
                user_id=callback_query.from_user.id,
                result_content=result.content if result else "No result",
                result_error=result.is_error if result else "No result",
            )

        except Exception as e:
            logger.error(
                "Error sending permission response to Claude via integration",
                error=str(e),
            )
            # Fallback to direct tmux approach
            await self._send_permission_response_via_tmux(option_number)

    async def _send_permission_response_via_tmux(self, option_number: str) -> None:
        """Fallback method: Send permission response directly via tmux."""
        logger.error(
            "Tmux fallback method called - this should not happen with new implementation"
        )

    def register_session(self, session_id: str, chat_id: int) -> None:
        """Register a session with a chat ID for targeted updates."""
        self.session_to_chat[session_id] = chat_id
        logger.info("Registered session", session_id=session_id, chat_id=chat_id)

    def record_telegram_prompt(self, user_id: int, prompt: str) -> None:
        """Record a prompt sent from Telegram to prevent echo."""
        self.last_telegram_prompts[user_id] = prompt
        logger.debug(
            "Recorded Telegram prompt", user_id=user_id, prompt_length=len(prompt)
        )

    async def initialize_subscriptions(self) -> None:
        """Initialize subscriptions for all allowed users."""
        if not self.settings.allowed_users:
            logger.warning("No allowed users configured")
            return

        logger.info(
            "Initializing hook message subscriptions for allowed users",
            user_count=len(self.settings.allowed_users),
        )

        # Subscribe all allowed users
        for user_id in self.settings.allowed_users:
            self.subscribed_users.add(user_id)

        # Notify users that they are subscribed
        notification_message = (
            "🔔 **Hook Monitoring Enabled**\n\n"
            "You are now subscribed to receive Claude conversation updates.\n"
            "You'll receive notifications when Claude uses tools or performs actions."
        )

        success_count = 0
        for user_id in self.subscribed_users:
            try:
                await self.bot.send_message(
                    chat_id=user_id,
                    text=notification_message,
                    parse_mode=ParseMode.MARKDOWN,
                )
                success_count += 1
                logger.info("Notified user about hook subscription", user_id=user_id)
            except Exception as e:
                logger.warning(
                    "Failed to notify user about subscription",
                    user_id=user_id,
                    error=str(e),
                )

        logger.info(
            "Hook subscription initialization complete",
            subscribed=len(self.subscribed_users),
            notified=success_count,
        )
