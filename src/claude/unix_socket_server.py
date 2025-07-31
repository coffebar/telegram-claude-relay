"""Unix socket server for secure IPC with Claude hooks."""

import asyncio
import json
import os

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

            # Check if this is a permission dialog
            if self._is_permission_dialog(message):
                logger.info("Detected permission dialog", message=message)

                # Get context from transcript file instead of tmux
                context = await self._get_permission_context_from_transcript(
                    transcript_path
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
                # Regular notification
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

            return {"continue": True}

        return {"status": "error", "message": f"Unknown hook type: {hook_type}"}

    def _is_permission_dialog(self, message: str) -> bool:
        """Check if a notification message indicates a permission dialog."""
        permission_indicators = [
            "needs your permission",
            "needs permission to use",
            "waiting for your input",
            "requires permission",
            "confirm",
            "asking to edit",
            "wants to edit",
            "edit the file",
            "update the file",
            "modify the file",
            "change the file",
        ]

        message_lower = message.lower()
        return any(indicator in message_lower for indicator in permission_indicators)

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
                for line in f:
                    try:
                        message = json.loads(line.strip())
                        recent_messages.append(message)
                    except json.JSONDecodeError:
                        continue

            # Look for the most recent assistant message that might contain the permission request context
            # Permission requests usually come after Claude tries to use a tool
            context_info = {
                "tool_use": None,
                "code_snippet": None,
                "new_code": None,
                "file_path": None,
            }

            # Check last few messages for context
            for msg in reversed(recent_messages[-10:]):  # Last 10 messages
                # Get the message content - it's nested in msg.message.content
                message_data = msg.get("message", {})
                content = message_data.get("content", "")

                # Log the message structure for debugging
                logger.debug(
                    "Checking message for context",
                    message_type=type(content),
                    content_preview=str(content)[:100],
                )

                if isinstance(content, list):
                    # Handle structured content
                    for item in content:
                        if isinstance(item, dict):
                            if item.get("type") == "tool_use":
                                tool_name = item.get("name", "")
                                tool_input = item.get("input", {})

                                logger.debug(
                                    "Found tool_use",
                                    tool_name=tool_name,
                                    tool_input_keys=list(tool_input.keys()),
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
                                    elif tool_name == "Write":
                                        content = tool_input.get("content", "")
                                        context_info["code_snippet"] = (
                                            content  # Full content, no truncation
                                        )
                                        context_info["new_code"] = (
                                            None  # Write doesn't have old/new, just content
                                        )

                                    return context_info  # Found relevant context

                                elif tool_name == "Bash":
                                    context_info["tool_use"] = tool_name
                                    context_info["code_snippet"] = tool_input.get(
                                        "command", ""
                                    )
                                    return context_info

                elif isinstance(content, str):
                    # Sometimes content might be a string with tool information
                    if "tool_use" in content.lower():
                        logger.debug(
                            "Found string content with tool_use",
                            content_preview=content[:200],
                        )

            return context_info if context_info["tool_use"] else None

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
