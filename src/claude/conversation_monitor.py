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
                # Include tool information for signature-based matching
                message_data = {
                    "type": "hook_notification",
                    "role": "system",
                    "content": formatted_message,
                    "timestamp": notification.get(
                        "timestamp", datetime.now().isoformat()
                    ),
                }

                # Add tool information for pre/post matching
                if notification.get("type") in ["pre_tool_use", "post_tool_use"]:
                    message_data.update(
                        {
                            "tool_name": notification.get("tool_name"),
                            "tool_params": notification.get("parameters", {}),
                            "notification_type": notification.get("type"),
                        }
                    )

                payload = {
                    "session_id": notification.get("session_id", "unknown"),
                    "message": message_data,
                    "timestamp": datetime.now().isoformat(),
                }

                await self.message_callback(payload)

        except Exception as e:
            logger.error("Error sending hook notification", error=str(e))

    async def send_permission_dialog(self, dialog_data: Dict[str, Any]) -> None:
        """Send permission dialog notification to Telegram with interactive buttons."""
        if not self.message_callback:
            return

        try:
            context = dialog_data.get("context", {})
            message = dialog_data.get("message", "")

            # Build detailed permission question with complete context
            question = self._build_permission_question(message, context)

            # Use parsed options from tmux pane or fallback
            options = self._get_permission_options(context)

            payload = {
                "session_id": dialog_data.get("session_id", "unknown"),
                "message": {
                    "type": "permission_dialog",
                    "role": "system",
                    "content": question,
                    "options": options,
                    "timestamp": dialog_data.get(
                        "timestamp", datetime.now().isoformat()
                    ),
                    "dialog_id": f"dialog_{datetime.now().timestamp()}",
                },
                "timestamp": datetime.now().isoformat(),
            }

            await self.message_callback(payload)

        except Exception as e:
            logger.error("Error sending permission dialog", error=str(e))

    def _get_permission_options(self, context: Dict[str, Any]) -> List[str]:
        """Get permission options from parsed tmux content or fallback."""
        if not context:
            return ["Allow", "Allow and don't ask again", "Deny"]

        # Use parsed options from tmux if available
        parsed_options = context.get("permission_options", [])
        if parsed_options:
            logger.info(
                "Using parsed permission options from tmux",
                options_count=len(parsed_options),
                options=parsed_options,
            )
            return parsed_options

        # Fallback: show parsing failure message instead of buttons
        logger.warning(
            "No permission options parsed from tmux, using parsing failure fallback",
            tool_use=context.get("tool_use"),
        )
        return ["Options parsing failed - please check tmux pane manually"]

    def _build_permission_question(
        self, message: str, context: Dict[str, Any], simplified: bool = False
    ) -> str:
        """Build a context-aware permission question for the user."""
        base_message = message or "Claude needs your permission"

        if not context:
            return f"🔐 **Permission Required**\n\n{base_message}"

        tool_use = context.get("tool_use")
        tool_input = context.get("tool_input", {})

        # Extract tool-specific fields from tool_input
        file_path = tool_input.get("file_path", "")

        # Extract old and new content based on tool type
        if tool_use == "Edit":
            code_snippet = tool_input.get("old_string", "")
            new_code = tool_input.get("new_string", "")
        elif tool_use == "MultiEdit":
            edits = tool_input.get("edits", [])
            if edits and isinstance(edits[0], dict):
                code_snippet = edits[0].get("old_string", "")
                new_code = edits[0].get("new_string", "")
            else:
                code_snippet = ""
                new_code = ""
        elif tool_use == "Write":
            code_snippet = ""  # Write doesn't have old content
            new_code = tool_input.get("content", "")
        elif tool_use == "Bash":
            code_snippet = tool_input.get("command", "")
            new_code = ""  # Bash doesn't have new content
        elif tool_use == "ExitPlanMode":
            code_snippet = tool_input.get("plan", "")
            new_code = ""  # Plan doesn't have new content
        elif tool_use == "Read":
            code_snippet = ""  # Read doesn't have code to show
            new_code = ""
        else:
            # Generic fallback - try to find any content
            code_snippet = (
                tool_input.get("content", "")
                or tool_input.get("command", "")
                or tool_input.get("plan", "")
            )
            new_code = ""

        # Detect programming language from file extension
        lang = self._detect_language(file_path)

        # Build question based on context and whether it's simplified
        if tool_use in ["Edit", "Update"]:
            if simplified:
                if file_path:
                    # Simplified version: just show permission request and filename
                    question = f"🔐 **Permission Required**\n\nClaude needs permission to edit `{file_path}`"
                else:
                    # Simplified version without filename
                    question = "🔐 **Permission Required**\n\nClaude needs permission to edit a file"
            else:
                # Full version: show all code details
                question = f"🔐 **Permission Required**\n\n{base_message}"
                if file_path:
                    question += f"\n\n📂 **File:** `{file_path}`"
                if code_snippet and new_code:
                    # Create diff showing the changes
                    diff_content = self._create_diff(code_snippet, new_code, file_path)
                    question += f"\n\n**Changes:**\n```diff\n{diff_content}\n```"
                elif code_snippet:
                    # Show code being removed if no new code available
                    question += f"\n\n**Removing:**\n```{lang}\n{code_snippet}\n```"
                elif new_code:
                    # Show code being added if no old code available
                    question += f"\n\n**Adding:**\n```{lang}\n{new_code}\n```"

        elif tool_use == "Write":
            if simplified:
                if file_path:
                    # Simplified version: just show permission request and filename
                    question = f"🔐 **Permission Required**\n\nClaude needs permission to write `{file_path}`"
                else:
                    # Simplified version without filename
                    question = "🔐 **Permission Required**\n\nClaude needs permission to write a file"
            else:
                # Full version: show all content details
                question = f"🔐 **Permission Required**\n\n{base_message}"
                if file_path:
                    question += f"\n\n📂 **File:** `{file_path}`"
                if code_snippet:
                    # Show full content, no truncation
                    question += (
                        f"\n\n**Content to write:**\n```{lang}\n{code_snippet}\n```"
                    )

        elif tool_use == "Bash":
            # Always show full command for bash (they're short anyway)
            question = f"🔐 **Permission Required**\n\n{base_message}"
            if code_snippet:
                # Show full command in code block
                question += f"\n\n**Command to execute:**\n```bash\n{code_snippet}\n```"

        elif tool_use == "MultiEdit":
            # Get edit count from context if available
            edit_count = context.get("edit_count", 0)
            edit_text = (
                f"{edit_count} changes" if edit_count > 0 else "multiple changes"
            )

            if simplified:
                if file_path:
                    # Simplified version: show permission request, filename, and edit count
                    question = f"🔐 **Permission Required**\n\nClaude needs permission to edit `{file_path}` ({edit_text})"
                else:
                    # Simplified version without filename but with edit count
                    question = f"🔐 **Permission Required**\n\nClaude needs permission to edit a file ({edit_text})"
            else:
                # Full version for MultiEdit with edit details
                question = f"🔐 **Permission Required**\n\n{base_message}"
                if file_path:
                    question += f"\n\n📂 **File:** `{file_path}` ({edit_text})"

                # Note: Removed truncated preview for MultiEdit - full content is available in Pre-tool hook

        elif tool_use == "ExitPlanMode":
            # Special handling for ExitPlanMode - show the plan content
            plan_content = context.get(
                "plan", code_snippet
            )  # Plan might be in 'plan' field or 'code_snippet'
            if plan_content:
                question = f"📋 **Plan Ready**\n\n{plan_content}\n\n**How would you like to proceed?**"
            else:
                question = "📋 **Plan Ready**\n\nClaude has finished planning and is ready to proceed.\n\n**How would you like to proceed?**"

        elif tool_use == "Read":
            # Read tool - show file to be read with range info
            question = f"🔐 **Permission Required**\n\n{base_message}"
            if file_path:
                question += f"\n\n📂 **File to read:** `{file_path}`"

                # Add range information if offset/limit are specified
                offset = tool_input.get("offset")
                limit = tool_input.get("limit")

                # Safely convert to int if needed
                try:
                    if offset is not None:
                        offset = int(offset)
                    if limit is not None:
                        limit = int(limit)

                    if offset is not None or limit is not None:
                        if offset is not None and limit is not None:
                            end_line = (
                                offset + limit - 1
                            )  # -1 because limit is inclusive
                            question += f"\n📍 **Range:** Lines {offset}-{end_line}"
                        elif offset is not None:
                            question += f"\n📍 **Starting from:** Line {offset}"
                        elif limit is not None:
                            question += f"\n📍 **Limit:** First {limit} lines"
                except (ValueError, TypeError):
                    # If conversion fails, just skip the range info
                    pass

        else:
            # Generic tool or unknown
            if simplified:
                if file_path:
                    # Try to provide a generic simplified message with filename
                    question = f"🔐 **Permission Required**\n\nClaude needs permission to use {tool_use} on `{file_path}`"
                elif tool_use:
                    # Just show tool name if available
                    question = f"🔐 **Permission Required**\n\nClaude needs permission to use {tool_use}"
                else:
                    # Fallback to base message
                    question = f"🔐 **Permission Required**\n\n{base_message}"
            else:
                # Full context version
                question = f"🔐 **Permission Required**\n\n{base_message}"
                if file_path:
                    question += f"\n\n📂 **File:** `{file_path}`"

        return question

    def _detect_language(self, file_path: str) -> str:
        """Detect programming language from file extension for syntax highlighting."""
        if not file_path:
            return ""

        # Map common file extensions to Telegram-supported language identifiers
        extension_map = {
            ".py": "python",
            ".js": "javascript",
            ".ts": "typescript",
            ".jsx": "jsx",
            ".tsx": "tsx",
            ".java": "java",
            ".c": "c",
            ".cpp": "cpp",
            ".cc": "cpp",
            ".cxx": "cpp",
            ".h": "c",
            ".hpp": "cpp",
            ".cs": "csharp",
            ".go": "go",
            ".rs": "rust",
            ".php": "php",
            ".rb": "ruby",
            ".sh": "bash",
            ".bash": "bash",
            ".zsh": "bash",
            ".fish": "bash",
            ".ps1": "powershell",
            ".sql": "sql",
            ".html": "html",
            ".css": "css",
            ".scss": "scss",
            ".sass": "sass",
            ".json": "json",
            ".xml": "xml",
            ".yaml": "yaml",
            ".yml": "yaml",
            ".toml": "toml",
            ".ini": "ini",
            ".cfg": "ini",
            ".conf": "ini",
            ".md": "markdown",
            ".markdown": "markdown",
            ".dockerfile": "dockerfile",
            ".makefile": "makefile",
            ".mk": "makefile",
        }

        # Get file extension
        import os

        _, ext = os.path.splitext(file_path.lower())

        # Special cases for files without extensions
        filename = os.path.basename(file_path.lower())
        if filename in ["dockerfile", "makefile", "vagrantfile", "jenkinsfile"]:
            return filename

        return extension_map.get(ext, "")

    def _create_diff(
        self, old_string: str, new_string: str, file_path: str = ""
    ) -> str:
        """Create a unified diff between old and new strings."""
        import difflib

        # Split strings into lines for difflib
        old_lines = old_string.splitlines(keepends=True)
        new_lines = new_string.splitlines(keepends=True)

        # Ensure lines end with newline for proper diff formatting
        if old_lines and not old_lines[-1].endswith("\n"):
            old_lines[-1] += "\n"
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines[-1] += "\n"

        # Generate unified diff
        diff_lines = difflib.unified_diff(
            old_lines,
            new_lines,
            n=3,  # Context lines
        )

        # Convert generator to list and join
        diff_content = "".join(diff_lines)

        # If diff is empty (strings are identical), show a message
        if not diff_content:
            return "# No changes detected"

        # Remove header lines (--- and +++) and trailing newlines
        lines = diff_content.splitlines()
        # Filter out header lines that start with --- or +++
        filtered_lines = [
            line
            for line in lines
            if not (line.startswith("---") or line.startswith("+++"))
        ]

        return "\n".join(filtered_lines)

    def _format_hook_notification(self, notification: Dict[str, Any]) -> Optional[str]:
        """Format hook notification for display."""
        notif_type = notification.get("type")

        if notif_type == "user_prompt":
            prompt = notification.get("prompt", "")
            return f"💬 **New Prompt:**\n```\n{prompt}\n```"

        elif notif_type == "pre_tool_use":
            tool_name = notification.get("tool_name", "Unknown")
            params = notification.get("parameters", {})

            # EVIDENCE-BASED formatting - only tools with verified parameter structures
            if tool_name == "Bash":
                # VERIFIED: {"command": "docker ps", "description": "Show running Docker containers", "timeout": 120000}
                command = params.get("command", "")
                description = params.get("description", "")
                timeout = params.get("timeout")

                # Format with full command in code block
                message = "💻 **Bash**"
                if description:
                    message += f" - {description}"

                # Add timeout info if specified
                if timeout and timeout != 120000:  # Only show if different from default
                    timeout_sec = timeout / 1000
                    message += f" (timeout: {timeout_sec}s)"

                message += f"\n```bash\n{command}\n```"
                return message

            elif tool_name == "LS":
                # VERIFIED: {"path": "/home/..."}
                path = params.get("path", "")
                return f"📂 **Listing:** `{path}`"

            elif tool_name == "Edit":
                # VERIFIED: {"file_path": "/path/to/file", "old_string": "...", "new_string": "...", "replace_all": false}
                file_path = params.get("file_path", "")
                old_string = params.get("old_string", "")
                new_string = params.get("new_string", "")
                replace_all = params.get("replace_all", False)

                # Get file language for syntax highlighting
                lang = self._detect_language(file_path)

                # Format the code changes
                message = f"✏️ **Editing:** `{file_path}`"
                if replace_all:
                    message += " (replace all)"
                message += "\n"

                # If both old and new strings exist, create a diff
                if old_string and new_string:
                    diff_content = self._create_diff(old_string, new_string, file_path)
                    message += f"\n**Changes:**\n```diff\n{diff_content}\n```"
                else:
                    # Show full old code
                    if old_string:
                        message += f"\n**Removing:**\n```{lang}\n{old_string}\n```\n"

                    # Show full new code
                    if new_string:
                        message += f"\n**Adding:**\n```{lang}\n{new_string}\n```"

                return message

            elif tool_name == "TodoWrite":
                # VERIFIED: {"todos": [{"content": "...", "status": "...", "priority": "...", "id": "..."}]}
                todos = params.get("todos", [])
                # Build detailed todo list
                message = "📝 **Managing todos:**\n"

                if todos:
                    message += "\n**Todo List:**\n"
                    for todo in todos:
                        content = todo.get("content", "")
                        status = todo.get("status", "unknown")
                        priority = todo.get("priority", "")

                        # Status emoji mapping
                        status_emoji = {
                            "pending": "⏳",
                            "in_progress": "🔄",
                            "completed": "✅",
                        }.get(status, "❓")

                        # Priority indicator
                        priority_indicator = ""
                        if priority == "high":
                            priority_indicator = " 🔥"
                        elif priority == "medium":
                            priority_indicator = " ⚡"

                        # Format each todo item
                        message += f"{status_emoji} {content}{priority_indicator}\n"

                return message.rstrip()

            elif tool_name == "Read":
                # VERIFIED: {"file_path": "/path/to/file", "offset": 162, "limit": 20}
                file_path = params.get("file_path", "")
                offset = params.get("offset")
                limit = params.get("limit")
                range_text = ""
                if offset is not None or limit is not None:
                    start = offset or 0
                    if limit is not None:
                        end = start + limit - 1  # -1 because limit is inclusive
                        range_text = f" (lines {start}-{end})"
                    else:
                        range_text = f" (from line {start})"
                return f"📖 **Reading:** `{file_path}`{range_text}"

            elif tool_name == "Write":
                # VERIFIED: {"file_path": "/path/to/file", "content": "..."}
                file_path = params.get("file_path", "")
                content = params.get("content", "")

                # Get file language for syntax highlighting
                lang = self._detect_language(file_path)

                # Format the message with full content
                message = f"✍️ **Writing:** `{file_path}`\n"
                if content:
                    message += f"\n**Content:**\n```{lang}\n{content}\n```"

                return message

            elif tool_name == "Grep":
                # VERIFIED: {"pattern": "search_pattern", "path": "/path", "output_mode": "content", "-A": 3, "-B": 2}
                pattern = params.get("pattern", "")
                path = params.get("path", "CWD")
                output_mode = params.get("output_mode", "files_with_matches")
                context_after = params.get("-A")
                context_before = params.get("-B")
                context_both = params.get("-C")
                case_insensitive = params.get("-i", False)
                line_numbers = params.get("-n", False)
                multiline = params.get("multiline", False)

                # Format with full pattern in code block
                message = f"🔍 **Grep in:** `{path}`"

                # Add mode and flags info
                mode_parts = []
                if output_mode != "files_with_matches":
                    mode_parts.append(output_mode)
                if case_insensitive:
                    mode_parts.append("case-insensitive")
                if line_numbers:
                    mode_parts.append("line numbers")
                if multiline:
                    mode_parts.append("multiline")

                # Add context info
                if context_both:
                    mode_parts.append(f"±{context_both} lines")
                elif context_before or context_after:
                    if context_before:
                        mode_parts.append(f"-{context_before} lines before")
                    if context_after:
                        mode_parts.append(f"+{context_after} lines after")

                if mode_parts:
                    message += f" ({', '.join(mode_parts)})"

                message += f"\n```regex\n{pattern}\n```"
                return message

            elif tool_name == "Glob":
                # VERIFIED: {"pattern": "*requirements*.txt", "path": "/home/..."}
                pattern = params.get("pattern", "")
                path = params.get("path", "")

                message = f"🗂️ **Finding files:** `{pattern}`"
                if path:
                    message += f" in `{path}`"
                return message

            elif tool_name == "MultiEdit":
                # VERIFIED: {"file_path": "/path/to/file", "edits": [{"old_string": "...", "new_string": "..."}]}
                file_path = params.get("file_path", "")
                edits = params.get("edits", [])

                # Get file language for syntax highlighting
                lang = self._detect_language(file_path)

                # Format the message
                message = f"✏️ **Multi-editing:** `{file_path}` ({len(edits)} changes)\n"

                # Show all edits
                for i, edit in enumerate(edits, 1):
                    old_string = edit.get("old_string", "")
                    new_string = edit.get("new_string", "")

                    message += f"\n**Edit {i}:**"

                    # If both old and new strings exist, create a diff
                    if old_string and new_string:
                        diff_content = self._create_diff(
                            old_string, new_string, file_path
                        )
                        message += f"\n```diff\n{diff_content}\n```"
                    else:
                        if old_string:
                            message += f"\n**Removing:**\n```{lang}\n{old_string}\n```"
                        if new_string:
                            message += f"\n**Adding:**\n```{lang}\n{new_string}\n```"

                    if i < len(edits):
                        message += "\n"

                return message

            elif tool_name == "WebSearch":
                # VERIFIED: {"query": "search terms"}
                query = params.get("query", "")
                return f"🌐 **Web Search:**\n```\n{query}\n```"

            elif tool_name == "ExitPlanMode":
                # VERIFIED: {"plan": "plan content"}
                return "📋 **Planning Complete**"

            elif tool_name == "Task":
                # VERIFIED: {"description": "task description", "prompt": "detailed prompt", "subagent_type": "agent type"}
                description = params.get("description", "")
                subagent_type = params.get("subagent_type", "")
                prompt = params.get("prompt", "")

                message = f"🤖 **Starting Task:** {description}"
                if subagent_type:
                    message += f"\n**Agent:** {subagent_type}"
                if prompt:
                    # Always show full prompt for Task tools - users want complete context
                    message += f"\n**Prompt:** {prompt}"
                return message

            elif tool_name == "WebFetch":
                # VERIFIED: {"url": "https://...", "prompt": "extraction prompt"}
                url = params.get("url", "")
                prompt = params.get("prompt", "")

                message = f"🌐 **Fetching:** {url}"
                if prompt:
                    message += f"\n**Extract:** {prompt}"
                return message

            else:
                # Unknown/unverified tool - generic display
                return f"🔧 **{tool_name}**"

        elif notif_type == "post_tool_use":
            tool_name = notification.get("tool_name", "Unknown")
            tool_response = notification.get("tool_response", {})

            # Format based on tool type with completion status
            if tool_name == "Edit":
                # Show which file was edited with additional info
                params = notification.get("parameters", {})
                file_path = params.get("file_path", "")

                message = "✅ **Edit completed**"
                if file_path:
                    message += f": `{file_path}`"

                # Add structured patch and modification info
                if isinstance(tool_response, dict):
                    user_modified = tool_response.get("userModified", False)
                    structured_patch = tool_response.get("structuredPatch")

                    if user_modified:
                        message += " ⚠️ *user modified*"

                    if structured_patch and isinstance(structured_patch, list):
                        patch_count = len(structured_patch)
                        if patch_count > 1:
                            message += f" ({patch_count} patches)"

                return message
            elif tool_name == "MultiEdit":
                # Show which file and how many edits with additional info
                params = notification.get("parameters", {})
                file_path = params.get("file_path", "")
                edits = params.get("edits", [])
                edit_count = len(edits)

                message = f"✅ **{edit_count} edit(s) completed**"
                if file_path:
                    message += f": `{file_path}`"

                # Add structured patch and modification info
                if isinstance(tool_response, dict):
                    user_modified = tool_response.get("userModified", False)
                    structured_patch = tool_response.get("structuredPatch")

                    if user_modified:
                        message += " ⚠️ *user modified*"

                    if structured_patch and isinstance(structured_patch, list):
                        patch_count = len(structured_patch)
                        if patch_count != edit_count:
                            message += f" ({patch_count} patches)"

                return message
            elif tool_name == "Write":
                # Show which file was created with size and patch info
                params = notification.get("parameters", {})
                file_path = params.get("file_path", "")
                content = params.get("content", "")

                message = "✅ **File written**"
                if file_path:
                    size_info = f" ({len(content)} chars)" if content else ""
                    message += f": `{file_path}`{size_info}"

                # Add structured patch info if available
                if isinstance(tool_response, dict):
                    structured_patch = tool_response.get("structuredPatch")
                    if structured_patch and isinstance(structured_patch, list):
                        patch_count = len(structured_patch)
                        if patch_count > 0:
                            message += f" ({patch_count} patches)"

                return message
            elif tool_name == "Read":
                return None  # Silenced for better UX
            elif tool_name == "LS":
                # Format LS tool response with actual directory contents
                message = "✅ **Directory listing:**"
                if isinstance(tool_response, str) and tool_response.strip():
                    # Truncate if too long (Telegram has message size limits)
                    content = tool_response.strip()
                    if len(content) > 3000:
                        content = content[:3000] + "\n... (truncated)"
                    message += f"\n```\n{content}\n```"
                return message
            elif tool_name == "Grep":
                # Format Grep results with match count and preview
                message = "✅ **Search completed**"
                if isinstance(tool_response, dict):
                    mode = tool_response.get("mode", "")
                    num_lines = tool_response.get("numLines", 0)

                    if mode == "files_with_matches":
                        filenames = tool_response.get("filenames", [])
                        if filenames:
                            message += f"\nFound in {len(filenames)} file(s):"
                            for fname in filenames[:10]:  # Show first 10
                                message += f"\n• `{fname}`"
                            if len(filenames) > 10:
                                message += f"\n... and {len(filenames) - 10} more"
                    elif mode == "count":
                        message += f"\nTotal matches: {num_lines}"
                    elif mode == "content" and num_lines > 0:
                        message += f"\nFound {num_lines} matching line(s)"
                        content = tool_response.get("content", "")
                        if content:
                            # Show preview of matches
                            preview = content[:500]
                            if len(content) > 500:
                                preview += "\n... (truncated)"
                            message += f"\n```\n{preview}\n```"
                    else:
                        message += "\nNo matches found"
                return message
            elif tool_name == "Bash":
                # For Bash commands, include the output and status
                message = "✅ **Command completed**"

                # Extract stdout and stderr from the tool response
                if isinstance(tool_response, dict):
                    stdout = tool_response.get("stdout", "").strip()
                    stderr = tool_response.get("stderr", "").strip()
                    interrupted = tool_response.get("interrupted", False)
                    return_code_interpretation = tool_response.get(
                        "returnCodeInterpretation", ""
                    )

                    # Add status indicators
                    status_parts = []
                    if interrupted:
                        status_parts.append("⚠️ interrupted")
                    if (
                        return_code_interpretation
                        and return_code_interpretation != "success"
                    ):
                        status_parts.append(f"status: {return_code_interpretation}")

                    if status_parts:
                        message += f" ({', '.join(status_parts)})"

                    if stdout:
                        message += f"\n\n**Output:**\n```\n{stdout}\n```"
                    if stderr:
                        message += f"\n\n**Error output:**\n```\n{stderr}\n```"

                return message
            elif tool_name == "Glob":
                # Glob completion with performance metrics
                message = "✅ **File search completed**"
                if isinstance(tool_response, dict):
                    num_files = tool_response.get("numFiles")
                    duration = tool_response.get("durationMs")
                    truncated = tool_response.get("truncated", False)

                    metrics = []
                    if num_files is not None:
                        metrics.append(f"{num_files} files")
                    if duration:
                        metrics.append(f"{duration}ms")

                    if metrics:
                        message += f" ({', '.join(metrics)})"

                    if truncated:
                        message += " ⚠️ *truncated*"

                return message
            elif tool_name == "WebSearch":
                # WebSearch completion with duration metrics
                message = "✅ **Web search completed**"
                if isinstance(tool_response, dict):
                    duration = tool_response.get("durationSeconds")
                    results = tool_response.get("results", [])

                    metrics = []
                    if len(results) > 0:
                        metrics.append(f"{len(results)} results")
                    if duration:
                        metrics.append(f"{duration:.1f}s")

                    if metrics:
                        message += f" ({', '.join(metrics)})"

                return message
            elif tool_name == "ExitPlanMode":
                # ExitPlanMode indicates planning is complete and ready to proceed
                return "✅ **Plan ready - awaiting approval**"
            elif tool_name == "Task":
                # Task completion with performance metrics
                message = "✅ **Task completed**"
                if isinstance(tool_response, dict):
                    duration = tool_response.get("totalDurationMs")
                    tokens = tool_response.get("totalTokens")
                    tool_count = tool_response.get("totalToolUseCount")
                    was_interrupted = tool_response.get("wasInterrupted", False)

                    metrics = []
                    if duration:
                        metrics.append(f"{duration}ms")
                    if tokens:
                        metrics.append(f"{tokens} tokens")
                    if tool_count:
                        metrics.append(f"{tool_count} tools")

                    if metrics:
                        message += f" ({', '.join(metrics)})"

                    if was_interrupted:
                        message += " ⚠️ *interrupted*"

                return message
            elif tool_name == "WebFetch":
                # WebFetch completion with response info
                message = "✅ **Fetch completed**"
                if isinstance(tool_response, dict):
                    url = tool_response.get("url", "")
                    code = tool_response.get("code")
                    duration = tool_response.get("durationMs")
                    bytes_fetched = tool_response.get("bytes")

                    if code:
                        message += f" ({code})"
                    if duration:
                        message += f" in {duration}ms"
                    if bytes_fetched:
                        message += f" - {bytes_fetched} bytes"

                return message
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
