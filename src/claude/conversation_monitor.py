"""Monitor Claude conversation transcripts and relay to Telegram."""

import asyncio
import json

from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import structlog

from ..config.settings import Settings


logger = structlog.get_logger()


class ConversationMonitor:
    """Monitors Claude conversation transcripts and relays messages to Telegram."""

    def __init__(self, config: Settings, message_callback: Optional[Callable] = None):
        self.config = config
        self.message_callback = message_callback
        self.last_processed_line = {}  # session_id -> last line number

    async def process_transcript(self, transcript_path: str, session_id: str) -> None:
        """Process a conversation transcript and send new messages to Telegram."""
        logger.info(
            "Processing transcript", path=transcript_path, session_id=session_id
        )
        try:
            path = Path(transcript_path).expanduser()
            if not path.exists():
                logger.warning("Transcript file not found", path=transcript_path)
                return

            # Get all messages first
            all_messages = await self._parse_all_messages(path, session_id)

            # Extract only the messages from the current conversation turn
            current_turn_messages = self._extract_current_turn(all_messages)

            logger.info(
                "Parsed messages from transcript",
                total_count=len(all_messages),
                current_turn_count=len(current_turn_messages),
                session_id=session_id,
            )

            if current_turn_messages:
                await self._relay_to_telegram(current_turn_messages, session_id)
            else:
                logger.info(
                    "No messages from current turn to relay", session_id=session_id
                )

        except Exception as e:
            logger.error(
                "Error processing transcript", error=str(e), path=transcript_path
            )

    async def _parse_all_messages(
        self, path: Path, session_id: str
    ) -> List[Dict[str, Any]]:
        """Parse all messages from transcript file."""
        messages = []

        try:
            with open(path) as f:
                for line_num, line in enumerate(f, 1):
                    try:
                        data = json.loads(line.strip())
                        message = self._extract_message_data(data)
                        if message:
                            messages.append(message)
                    except json.JSONDecodeError:
                        logger.debug("Skipping invalid JSON line", line_num=line_num)

        except Exception as e:
            logger.error("Error reading transcript", error=str(e))

        return messages

    def _extract_current_turn(
        self, messages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Extract only messages from the current conversation turn.

        A conversation turn starts with the last user message and includes
        all subsequent assistant messages until the end.
        """
        if not messages:
            return []

        # Find the last user message
        last_user_index = -1
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                last_user_index = i
                break

        if last_user_index == -1:
            # No user message found, return all assistant messages
            return [m for m in messages if m.get("role") == "assistant"]

        # Return all messages from the last user message onwards
        current_turn = messages[last_user_index:]

        # Filter to only include the last user message and assistant responses
        # This helps consolidate multiple assistant chunks into meaningful updates
        result = []

        # Add the user message
        result.append(current_turn[0])

        # Consolidate assistant messages
        assistant_content = []
        assistant_tools = []

        for msg in current_turn[1:]:
            if msg.get("role") == "assistant":
                if msg.get("content"):
                    assistant_content.append(msg["content"])
                if msg.get("tool_calls"):
                    assistant_tools.extend(msg["tool_calls"])

        # Create a single consolidated assistant message if there's content
        if assistant_content or assistant_tools:
            consolidated_msg = {
                "type": "message",
                "role": "assistant",
                "content": "\n".join(assistant_content),
                "tool_calls": assistant_tools,
                "timestamp": current_turn[-1].get("timestamp"),
                "metadata": current_turn[-1].get("metadata", {}),
            }
            result.append(consolidated_msg)

        return result

    def _extract_message_data(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Extract relevant message data from transcript entry."""
        # Claude Code transcript format (new):
        # {"type": "user", "message": {"role": "user", "content": "..."}, ...}
        # {"type": "assistant", "message": {"content": [{"type": "text", "text": "..."}]}, ...}

        msg_type = data.get("type")
        message = data.get("message", {})

        logger.debug(
            "Extracting message", msg_type=msg_type, available_keys=list(data.keys())
        )

        # Only process user and assistant messages
        if msg_type not in ["user", "assistant"]:
            logger.debug("Skipping message type", msg_type=msg_type)
            return None

        # Skip tool results (they have type "user" but contain tool_result)
        if msg_type == "user" and isinstance(message.get("content"), list):
            content_list = message.get("content", [])
            if (
                content_list
                and isinstance(content_list[0], dict)
                and content_list[0].get("type") == "tool_result"
            ):
                logger.debug("Skipping tool result message")
                return None

        # Extract content based on message type
        content = ""
        tool_calls = []

        if msg_type == "user":
            content = message.get("content", "")
        elif msg_type == "assistant":
            # Assistant messages have content as array
            content_array = message.get("content", [])
            if isinstance(content_array, list):
                for item in content_array:
                    if isinstance(item, dict):
                        if item.get("type") == "text":
                            content += item.get("text", "")
                        elif item.get("type") == "tool_use":
                            tool_calls.append(
                                {
                                    "name": item.get("name"),
                                    "parameters": item.get("input", {}),
                                }
                            )
            elif isinstance(content_array, str):
                content = content_array

        # Skip if no text content (could be tool-only message)
        if not content and not tool_calls:
            logger.debug("No content or tool calls found", msg_type=msg_type)
            return None

        result = {
            "type": "message",
            "role": message.get("role", msg_type),  # Use role from message if available
            "content": content,
            "timestamp": data.get("timestamp", datetime.now().isoformat()),
            "tool_calls": tool_calls,
            "metadata": {
                "session_id": data.get("sessionId"),
                "uuid": data.get("uuid"),
                "request_id": data.get("requestId"),
            },
        }

        logger.debug(
            "Extracted message",
            role=result["role"],
            has_content=bool(content),
            tool_count=len(tool_calls),
        )
        return result

    async def _relay_to_telegram(
        self, messages: List[Dict[str, Any]], session_id: str
    ) -> None:
        """Send messages to Telegram via callback."""
        if not self.message_callback:
            logger.debug("No message callback configured, skipping relay")
            return

        for message in messages:
            try:
                payload = {
                    "session_id": session_id,
                    "message": message,
                    "timestamp": datetime.now().isoformat(),
                }

                # Call the callback function directly
                await self.message_callback(payload)

            except Exception as e:
                logger.error("Error relaying to Telegram", error=str(e))

    async def send_hook_notification(self, notification: Dict[str, Any]) -> None:
        """Send real-time hook notifications to Telegram."""
        if not self.message_callback:
            return

        try:
            # Create a formatted message based on notification type
            formatted_message = self._format_hook_notification(notification)

            if formatted_message:
                payload = {
                    "session_id": notification.get("session_id", "unknown"),
                    "message": {
                        "type": "hook_notification",
                        "role": "system",
                        "content": formatted_message,
                        "timestamp": notification.get(
                            "timestamp", datetime.now().isoformat()
                        ),
                    },
                    "timestamp": datetime.now().isoformat(),
                }

                await self.message_callback(payload)

        except Exception as e:
            logger.error("Error sending hook notification", error=str(e))

    def _format_hook_notification(self, notification: Dict[str, Any]) -> Optional[str]:
        """Format hook notification for display."""
        notif_type = notification.get("type")

        if notif_type == "user_prompt":
            prompt = notification.get("prompt", "")
            if len(prompt) > 200:
                prompt = prompt[:200] + "..."
            return f"💬 **New Prompt:**\n```\n{prompt}\n```"

        elif notif_type == "pre_tool_use":
            tool_name = notification.get("tool_name", "Unknown")
            params = notification.get("parameters", {})

            # EVIDENCE-BASED formatting - only tools with verified parameter structures
            if tool_name == "Bash":
                # VERIFIED: {"command": "docker ps", "description": "Show running Docker containers"}
                command = params.get("command", "")
                description = params.get("description", "")
                if len(command) > 80:
                    command = command[:80] + "..."
                desc_text = f" - {description}" if description else ""
                return f"💻 **Bash:** `{command}`{desc_text}"

            elif tool_name == "LS":
                # VERIFIED: {"path": "/home/..."}
                path = params.get("path", "")
                return f"📂 **Listing:** `{path}`"

            elif tool_name == "Edit":
                # VERIFIED: {"file_path": "/path/to/file", "old_string": "...", "new_string": "..."}
                file_path = params.get("file_path", "")
                old_string = params.get("old_string", "")
                if old_string and len(old_string) > 50:
                    old_string = old_string[:50] + "..."
                replace_text = f" - replacing: `{old_string}`" if old_string else ""
                return f"✏️ **Editing:** `{file_path}`{replace_text}"

            elif tool_name == "TodoWrite":
                # VERIFIED: {"todos": [{"content": "...", "status": "...", "priority": "...", "id": "..."}]}
                todos = params.get("todos", [])
                todo_count = len(todos)
                return f"📝 **Managing todos:** {todo_count} items"

            elif tool_name == "Read":
                # VERIFIED: {"file_path": "/path/to/file", "offset": 162, "limit": 20}
                file_path = params.get("file_path", "")
                offset = params.get("offset")
                limit = params.get("limit")
                range_text = ""
                if offset is not None or limit is not None:
                    range_text = (
                        f" (lines {offset or 0}-{(offset or 0) + (limit or 'end')})"
                    )
                return f"📖 **Reading:** `{file_path}`{range_text}"

            elif tool_name == "Write":
                # VERIFIED: {"file_path": "/path/to/file", "content": "..."}
                file_path = params.get("file_path", "")
                content = params.get("content", "")
                size_text = f" ({len(content)} chars)" if content else ""
                return f"✍️ **Writing:** `{file_path}`{size_text}"

            elif tool_name == "Grep":
                # VERIFIED: {"pattern": "search_pattern", "path": "/path", "output_mode": "content"}
                pattern = params.get("pattern", "")
                path = params.get("path", "")
                output_mode = params.get("output_mode", "files_with_matches")
                if len(pattern) > 50:
                    pattern = pattern[:50] + "..."
                mode_text = (
                    f" ({output_mode})" if output_mode != "files_with_matches" else ""
                )
                return f"🔍 **Searching:** `{pattern}` in `{path}`{mode_text}"

            elif tool_name == "Glob":
                # VERIFIED: {"pattern": "*requirements*.txt"}
                pattern = params.get("pattern", "")
                return f"🗂️ **Finding files:** `{pattern}`"

            elif tool_name == "MultiEdit":
                # VERIFIED: {"file_path": "/path/to/file", "edits": [{"old_string": "...", "new_string": "..."}]}
                file_path = params.get("file_path", "")
                edits = params.get("edits", [])
                edit_count = len(edits)
                return f"✏️ **Multi-editing:** `{file_path}` ({edit_count} changes)"

            elif tool_name == "WebSearch":
                # VERIFIED: {"query": "search terms"}
                query = params.get("query", "")
                if len(query) > 60:
                    query = query[:60] + "..."
                return f"🌐 **Web Search:** `{query}`"

            else:
                # Unknown/unverified tool - generic display
                return f"🔧 **{tool_name}**"

        elif notif_type == "post_tool_use":
            tool_name = notification.get("tool_name", "Unknown")

            # Format based on tool type with completion status
            if tool_name == "Edit":
                return "✅ **Edit completed**"
            elif tool_name == "Write":
                return "✅ **File created**"
            elif tool_name == "Read":
                return "✅ **Read completed**"
            elif tool_name == "Bash":
                return "✅ **Command completed**"
            elif tool_name == "Grep":
                return "✅ **Search completed**"
            elif tool_name == "Glob":
                return "✅ **File search completed**"
            elif tool_name == "MultiEdit":
                return "✅ **Multi-edit completed**"
            elif tool_name == "WebSearch":
                return "✅ **Web search completed**"
            else:
                return f"✅ **{tool_name} completed**"

        return None


class ConversationHookHandler:
    """Handles Claude Code hook events for conversation monitoring."""

    def __init__(self, monitor: ConversationMonitor):
        self.monitor = monitor

    async def handle_stop_hook(self, hook_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle Stop hook event from Claude Code."""
        session_id = hook_data.get("session_id")
        transcript_path = hook_data.get("transcript_path")

        if not session_id or not transcript_path:
            return {"status": "error", "message": "Missing required fields"}

        # Process transcript in background to avoid blocking
        asyncio.create_task(
            self.monitor.process_transcript(transcript_path, session_id)
        )

        return {"status": "ok", "continue": False}
