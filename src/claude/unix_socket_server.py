"""Unix socket server for secure IPC with Claude hooks."""

import asyncio
import json
import os
import time

from pathlib import Path
from typing import Any, Dict, Optional

import structlog

from ..config.settings import Settings
from .conversation_monitor import ConversationMonitor


logger = structlog.get_logger()


class UnixSocketServer:
    """Unix domain socket server for receiving Claude hook events."""

    def __init__(self, config: Settings, conversation_monitor: ConversationMonitor):
        self.config = config
        self.monitor = conversation_monitor
        self.socket_path = Path.home() / ".claude" / "telegram-relay.sock"
        self.server: Optional[asyncio.Server] = None
        # Track recent PreToolUse hooks to distinguish permission vs idle notifications
        self.recent_tool_usage: Dict[str, float] = {}  # session_id -> timestamp
        # Store recent tool context for fallback when transcript parsing fails
        self.recent_tool_context: Dict[str, Dict[str, Any]] = (
            {}
        )  # session_id -> tool_context

    async def start(self):
        """Start the Unix socket server."""
        # Ensure socket directory exists
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)

        # Remove existing socket file if it exists
        if self.socket_path.exists():
            self.socket_path.unlink()

        # Create Unix socket server
        self.server = await asyncio.start_unix_server(
            self.handle_client, path=str(self.socket_path)
        )

        # Set permissions to be restrictive (only owner can access)
        os.chmod(self.socket_path, 0o600)

        logger.info(f"Unix socket server started at {self.socket_path}")

        async with self.server:
            await self.server.serve_forever()

    async def handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        """Handle incoming connections."""
        try:
            # Read data from the socket
            data = await reader.read(65536)  # 64KB max

            if not data:
                return

            # Parse JSON data
            try:
                hook_data = json.loads(data.decode("utf-8"))
            except json.JSONDecodeError as e:
                logger.error("Invalid JSON received", error=str(e))
                response = {"status": "error", "message": "Invalid JSON"}
                writer.write(json.dumps(response).encode("utf-8"))
                await writer.drain()
                return

            # Process the hook event
            response = await self.process_hook_event(hook_data)

            # Send response
            writer.write(json.dumps(response).encode("utf-8"))
            await writer.drain()

        except Exception as e:
            logger.error("Error handling client connection", error=str(e))
        finally:
            writer.close()
            await writer.wait_closed()

    async def process_hook_event(self, hook_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process incoming hook event."""
        hook_type = hook_data.get("hook_event_name")
        logger.info(
            "Received hook event", hook_type=hook_type, data_keys=list(hook_data.keys())
        )

        if hook_type == "Stop":
            session_id = hook_data.get("session_id")
            transcript_path = hook_data.get("transcript_path")

            logger.info(
                "Processing Stop hook",
                session_id=session_id,
                transcript_path=transcript_path,
            )

            if not session_id or not transcript_path:
                logger.error(
                    "Missing required fields",
                    session_id=session_id,
                    transcript_path=transcript_path,
                )
                return {"status": "error", "message": "Missing required fields"}

            # Process transcript in background
            asyncio.create_task(
                self.monitor.process_transcript(transcript_path, session_id)
            )

            return {"status": "ok", "continue": True}

        elif hook_type == "UserPromptSubmit":
            # Handle user prompt submission
            prompt = hook_data.get("prompt", "")
            session_id = hook_data.get("session_id", "unknown")

            logger.info(
                "Processing UserPromptSubmit hook",
                session_id=session_id,
                prompt_length=len(prompt),
            )

            # Send notification about new prompt
            asyncio.create_task(
                self.monitor.send_hook_notification(
                    {
                        "type": "user_prompt",
                        "session_id": session_id,
                        "prompt": prompt,
                        "timestamp": hook_data.get("timestamp"),
                    }
                )
            )

            return {"continue": True}

        elif hook_type == "PreToolUse":
            # Handle pre-tool use notification
            tool_name = hook_data.get("tool_name", "")
            tool_input = hook_data.get("tool_input", {})
            session_id = hook_data.get("session_id", "unknown")

            # Track this tool usage for permission dialog detection
            self.recent_tool_usage[session_id] = time.time()

            # Store tool context for fallback when transcript parsing fails
            if tool_name in ["Edit", "MultiEdit", "Write", "Bash"]:
                self.recent_tool_context[session_id] = {
                    "tool_use": tool_name,
                    "tool_input": tool_input,
                    "timestamp": time.time(),
                }
                logger.debug(
                    "Stored tool context for fallback",
                    session_id=session_id,
                    tool_name=tool_name,
                    context_keys=list(self.recent_tool_context[session_id].keys()),
                )

            logger.info(
                "Processing PreToolUse hook",
                session_id=session_id,
                tool_name=tool_name,
                tool_input_keys=(
                    list(tool_input.keys())
                    if isinstance(tool_input, dict)
                    else "non-dict"
                ),
                tool_input_full=tool_input,
            )  # Log the complete structure for analysis

            # Send notification about tool use
            asyncio.create_task(
                self.monitor.send_hook_notification(
                    {
                        "type": "pre_tool_use",
                        "session_id": session_id,
                        "tool_name": tool_name,
                        "parameters": tool_input,  # Pass tool_input as parameters
                        "timestamp": hook_data.get("timestamp"),
                    }
                )
            )

            return {"continue": True}

        elif hook_type == "PostToolUse":
            # Handle post-tool use notification
            tool_name = hook_data.get("tool_name", "")
            tool_response = hook_data.get("tool_response", {})
            tool_input = hook_data.get("tool_input", {})
            session_id = hook_data.get("session_id", "unknown")

            logger.info(
                "Processing PostToolUse hook",
                session_id=session_id,
                tool_name=tool_name,
                has_response=bool(tool_response),
                response_keys=(
                    list(tool_response.keys())
                    if isinstance(tool_response, dict)
                    else "non-dict"
                ),
                tool_input_full=tool_input,  # Log input for reference
                tool_response_full=tool_response,
            )  # Log complete response structure

            # Send notification about tool result
            asyncio.create_task(
                self.monitor.send_hook_notification(
                    {
                        "type": "post_tool_use",
                        "session_id": session_id,
                        "tool_name": tool_name,
                        "parameters": tool_input,  # Include original parameters
                        "result_preview": (
                            str(tool_response)[:200] if tool_response else None
                        ),
                        "tool_response": tool_response,  # Pass full response
                        "timestamp": hook_data.get("timestamp"),
                    }
                )
            )

            return {"continue": True}

        elif hook_type == "Notification":
            # Handle Claude notification events (including permission dialogs)
            message = hook_data.get("message", "")
            session_id = hook_data.get("session_id", "unknown")
            transcript_path = hook_data.get("transcript_path", "")

            logger.info(
                "Processing Notification hook",
                session_id=session_id,
                message=message,
                transcript_path=transcript_path,
                full_data=hook_data,
            )

            # Check if this is a permission dialog based on recent tool usage context
            if self._is_permission_dialog(session_id):
                logger.info(
                    "Detected permission dialog based on recent tool usage",
                    message=message,
                    session_id=session_id,
                )

                # Primary: Use recent PreToolUse hook data (most accurate for permission dialogs)
                context = self._get_fallback_context(session_id)

                if context:
                    logger.info(
                        "Using PreToolUse hook context (primary source)",
                        session_id=session_id,
                        hook_context=context,
                    )
                else:
                    # Secondary fallback: Try transcript parsing (for edge cases)
                    logger.info(
                        "No recent PreToolUse context, trying transcript parsing"
                    )
                    context = await self._get_permission_context_from_transcript(
                        transcript_path
                    )
                    if context:
                        logger.info(
                            "Using transcript context (fallback)",
                            session_id=session_id,
                            transcript_context=context,
                        )

                logger.info(
                    "Permission context extracted",
                    context=context,
                    message=message,
                    transcript_path=transcript_path,
                )

                # Send permission dialog notification
                asyncio.create_task(
                    self.monitor.send_permission_dialog(
                        {
                            "type": "permission_dialog",
                            "session_id": session_id,
                            "message": message,
                            "context": context,
                            "timestamp": hook_data.get("timestamp"),
                        }
                    )
                )
            else:
                # Idle timeout or other notification (no recent tool usage)
                logger.info(
                    "Processing idle timeout or regular notification",
                    message=message,
                    session_id=session_id,
                )
                asyncio.create_task(
                    self.monitor.send_hook_notification(
                        {
                            "type": "notification",
                            "session_id": session_id,
                            "message": message,
                            "timestamp": hook_data.get("timestamp"),
                        }
                    )
                )

            # Clean up old tool usage entries periodically
            self._cleanup_old_tool_usage()

            return {"continue": True}

        return {"status": "error", "message": f"Unknown hook type: {hook_type}"}

    def _is_permission_dialog(self, session_id: str) -> bool:
        """Check if a notification is a permission dialog based on recent tool usage context.

        Permission dialogs occur when Claude needs permission after attempting to use a tool.
        Idle timeout notifications occur when there's no recent tool usage.
        """
        # Check if there was recent PreToolUse activity (within last 30 seconds)
        recent_tool_time = self.recent_tool_usage.get(session_id)
        if not recent_tool_time:
            return False

        # If tool usage was recent, this is likely a permission dialog
        time_since_tool = time.time() - recent_tool_time
        is_recent = (
            time_since_tool <= 60
        )  # 60 second window (extended for transcript processing)

        logger.info(
            "Permission dialog detection",
            session_id=session_id,
            has_recent_tool=bool(recent_tool_time),
            time_since_tool=time_since_tool if recent_tool_time else None,
            is_permission_dialog=is_recent,
            recent_tool_time=recent_tool_time,
            current_time=time.time(),
        )

        return is_recent

    def _get_fallback_context(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get permission context from recent PreToolUse hook data (primary source for permission dialogs)."""
        recent_context = self.recent_tool_context.get(session_id)
        if not recent_context:
            logger.debug("No PreToolUse context available", session_id=session_id)
            return None

        # Check if context is still recent (within 90 seconds)
        context_age = time.time() - recent_context.get("timestamp", 0)
        if context_age > 90:
            logger.debug(
                "PreToolUse context too old",
                session_id=session_id,
                context_age=context_age,
            )
            return None

        tool_name = recent_context.get("tool_use")
        tool_input = recent_context.get("tool_input", {})

        logger.info(
            "Processing PreToolUse hook context",
            session_id=session_id,
            tool_name=tool_name,
            tool_input_keys=list(tool_input.keys()) if tool_input else [],
            context_age_seconds=context_age,
        )

        # Build context similar to transcript parsing
        context_info = {
            "tool_use": tool_name,
            "code_snippet": None,
            "new_code": None,
            "file_path": None,
        }

        if tool_name in ["Edit", "MultiEdit", "Write"]:
            context_info["file_path"] = tool_input.get("file_path", "")

            if tool_name == "Edit":
                context_info["code_snippet"] = tool_input.get("old_string", "")
                context_info["new_code"] = tool_input.get("new_string", "")
            elif tool_name == "MultiEdit":
                # MultiEdit has edits array with multiple old_string/new_string pairs
                edits = tool_input.get("edits", [])
                if edits:
                    # For permission dialog, show summary of first edit as preview
                    first_edit = edits[0]
                    context_info["code_snippet"] = first_edit.get("old_string", "")
                    context_info["new_code"] = first_edit.get("new_string", "")
                    # Store edit count for permission dialog formatting
                    context_info["edit_count"] = len(edits)
                    logger.info(
                        "MultiEdit PreToolUse context extracted",
                        session_id=session_id,
                        edit_count=len(edits),
                        first_edit_preview=str(first_edit.get("old_string", ""))[:100],
                    )
                else:
                    context_info["code_snippet"] = None
                    context_info["new_code"] = None
                    context_info["edit_count"] = 0
            elif tool_name == "Write":
                context_info["code_snippet"] = tool_input.get("content", "")
                context_info["new_code"] = (
                    None  # Write doesn't have old/new, just content
                )
        elif tool_name == "Bash":
            context_info["code_snippet"] = tool_input.get("command", "")

        logger.info(
            "PreToolUse hook context built successfully",
            session_id=session_id,
            context_info=context_info,
        )

        return context_info

    def _cleanup_old_tool_usage(self, max_age_seconds: int = 300) -> None:
        """Clean up old tool usage entries to prevent memory leaks."""
        current_time = time.time()

        # Clean up tool usage timestamps
        expired_sessions = [
            session_id
            for session_id, timestamp in self.recent_tool_usage.items()
            if current_time - timestamp > max_age_seconds
        ]

        for session_id in expired_sessions:
            del self.recent_tool_usage[session_id]
            logger.debug("Cleaned up old tool usage entry", session_id=session_id)

        # Clean up tool context cache
        expired_context_sessions = [
            session_id
            for session_id, context_data in self.recent_tool_context.items()
            if current_time - context_data.get("timestamp", 0) > max_age_seconds
        ]

        for session_id in expired_context_sessions:
            del self.recent_tool_context[session_id]
            logger.debug("Cleaned up old tool context entry", session_id=session_id)

    async def _get_permission_context_from_transcript(
        self, transcript_path: str
    ) -> Optional[Dict[str, Any]]:
        """Get permission context from transcript file."""
        try:
            import json

            from pathlib import Path

            path = Path(transcript_path)
            if not path.exists():
                logger.warning("Transcript file not found", path=transcript_path)
                return None

            # Read the transcript file to get recent context
            recent_messages = []

            with open(path) as f:
                for line_num, line in enumerate(f, 1):
                    try:
                        message = json.loads(line.strip())
                        recent_messages.append(message)
                    except json.JSONDecodeError:
                        logger.debug(
                            "Skipping invalid JSON line in transcript",
                            line_number=line_num,
                            transcript_path=transcript_path,
                        )
                        continue

            logger.info(
                "Transcript parsing completed",
                transcript_path=transcript_path,
                total_messages=len(recent_messages),
                searching_last_messages=min(20, len(recent_messages)),
            )

            # Look for the most recent assistant message that might contain the permission request context
            # Permission requests usually come after Claude tries to use a tool
            context_info = {
                "tool_use": None,
                "code_snippet": None,
                "new_code": None,
                "file_path": None,
            }

            # Check last few messages for context
            messages_to_search = recent_messages[
                -20:
            ]  # Last 20 messages (extended search)
            logger.info(
                "Starting context extraction",
                messages_to_check=len(messages_to_search),
                transcript_path=transcript_path,
            )

            for msg_idx, msg in enumerate(reversed(messages_to_search)):
                # Get the message content - it's nested in msg.message.content
                message_data = msg.get("message", {})
                content = message_data.get("content", "")
                msg_type = msg.get("type", "unknown")

                # Log the message structure for debugging
                logger.debug(
                    "Checking message for context",
                    message_index=msg_idx,
                    message_type=msg_type,
                    content_type=type(content).__name__,
                    content_preview=str(content)[:100] if content else "empty",
                    has_content_list=isinstance(content, list),
                    content_length=len(content) if isinstance(content, list) else 0,
                )

                if isinstance(content, list):
                    # Handle structured content
                    logger.debug(
                        "Processing structured content list",
                        content_items=len(content),
                        message_index=msg_idx,
                    )

                    for item_idx, item in enumerate(content):
                        if isinstance(item, dict):
                            item_type = item.get("type", "")
                            logger.debug(
                                "Processing content item",
                                item_index=item_idx,
                                item_type=item_type,
                                item_keys=list(item.keys()),
                            )

                            if item.get("type") == "tool_use":
                                tool_name = item.get("name", "")
                                tool_input = item.get("input", {})

                                logger.info(
                                    "Found tool_use in transcript",
                                    message_index=msg_idx,
                                    item_index=item_idx,
                                    tool_name=tool_name,
                                    tool_input_keys=list(tool_input.keys()),
                                    tool_input=tool_input,  # Log complete input for debugging
                                )

                                # Extract relevant context based on tool type
                                if tool_name in ["Edit", "MultiEdit", "Write"]:
                                    context_info["tool_use"] = tool_name
                                    context_info["file_path"] = tool_input.get(
                                        "file_path", ""
                                    )

                                    if tool_name == "Edit":
                                        context_info["code_snippet"] = tool_input.get(
                                            "old_string", ""
                                        )
                                        context_info["new_code"] = tool_input.get(
                                            "new_string", ""
                                        )
                                    elif tool_name == "MultiEdit":
                                        # MultiEdit has edits array with multiple old_string/new_string pairs
                                        edits = tool_input.get("edits", [])
                                        if edits:
                                            # For permission dialog, show summary of first edit as preview
                                            first_edit = edits[0]
                                            context_info["code_snippet"] = (
                                                first_edit.get("old_string", "")
                                            )
                                            context_info["new_code"] = first_edit.get(
                                                "new_string", ""
                                            )
                                            # Store edit count for permission dialog formatting
                                            context_info["edit_count"] = len(edits)
                                            logger.info(
                                                "MultiEdit transcript context extracted",
                                                message_index=msg_idx,
                                                edit_count=len(edits),
                                                first_edit_preview=str(
                                                    first_edit.get("old_string", "")
                                                )[:100],
                                            )
                                        else:
                                            context_info["code_snippet"] = None
                                            context_info["new_code"] = None
                                            context_info["edit_count"] = 0
                                    elif tool_name == "Write":
                                        content = tool_input.get("content", "")
                                        context_info["code_snippet"] = (
                                            content  # Full content, no truncation
                                        )
                                        context_info["new_code"] = (
                                            None  # Write doesn't have old/new, just content
                                        )

                                    logger.info(
                                        "Context extraction successful",
                                        tool_name=tool_name,
                                        file_path=context_info.get("file_path"),
                                        has_code_snippet=bool(
                                            context_info.get("code_snippet")
                                        ),
                                        context_info=context_info,
                                    )
                                    return context_info  # Found relevant context

                                elif tool_name == "Bash":
                                    context_info["tool_use"] = tool_name
                                    context_info["code_snippet"] = tool_input.get(
                                        "command", ""
                                    )
                                    logger.info(
                                        "Context extraction successful (Bash)",
                                        tool_name=tool_name,
                                        command=context_info.get("code_snippet"),
                                        context_info=context_info,
                                    )
                                    return context_info

                elif isinstance(content, str):
                    # Sometimes content might be a string with tool information
                    if "tool_use" in content.lower():
                        logger.debug(
                            "Found string content with tool_use",
                            content_preview=content[:200],
                        )

            # Log final result
            if context_info["tool_use"]:
                logger.info(
                    "Context extraction completed successfully",
                    context_info=context_info,
                )
                return context_info
            else:
                logger.warning(
                    "Context extraction failed - no tool_use found in transcript",
                    transcript_path=transcript_path,
                    messages_searched=len(messages_to_search),
                    total_messages=len(recent_messages),
                )
                return None

        except Exception as e:
            logger.error(
                "Error getting permission context from transcript", error=str(e)
            )
            return None

    async def stop(self):
        """Stop the server and cleanup."""
        if self.server:
            self.server.close()
            await self.server.wait_closed()

        # Remove socket file
        if self.socket_path.exists():
            self.socket_path.unlink()
