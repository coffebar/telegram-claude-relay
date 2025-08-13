"""Unix socket server for secure IPC with Claude hooks."""

import asyncio
import json
import os
import time

from pathlib import Path
from typing import Any, Dict, List, Optional

import structlog

from ..config.settings import Settings
from .conversation_monitor import ConversationMonitor


logger = structlog.get_logger()


class UnixSocketServer:
    """Unix domain socket server for receiving Claude hook events."""

    def __init__(self, config: Settings, conversation_monitor: ConversationMonitor):
        self.config = config
        self.monitor = conversation_monitor
        self.socket_path = Path.cwd() / config.socket_path
        self.server: Optional[asyncio.Server] = None
        # Track recent PreToolUse hooks to distinguish permission vs idle notifications
        self.recent_tool_usage: Dict[str, float] = {}  # session_id -> timestamp
        # Store recent tool context for fallback when transcript parsing fails
        self.recent_tool_context: Dict[str, Dict[str, Any]] = (
            {}
        )  # session_id -> tool_context
        self.tmux_client = None  # Will be set by the facade
        self.target_cwd = None  # CWD of the Claude process we're monitoring

    def set_tmux_client(self, tmux_client):
        """Set the tmux client reference for CWD checking."""
        self.tmux_client = tmux_client

    async def initialize_target_cwd(self):
        """Get and store the CWD of the Claude process we're monitoring."""
        if self.tmux_client:
            try:
                self.target_cwd = await self.tmux_client.get_pane_cwd()
                logger.info(f"Initialized target CWD: {self.target_cwd}")
            except Exception as e:
                logger.warning(f"Could not get tmux pane CWD: {e}")
                self.target_cwd = None

    async def start(self):
        """Start the Unix socket server."""

        # Remove existing socket file if it exists
        if self.socket_path.exists():
            self.socket_path.unlink()

        # Get the target CWD before starting
        await self.initialize_target_cwd()

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

    def _truncate_params(
        self, params: Dict[str, Any], max_length: int = 200
    ) -> Dict[str, Any]:
        """Truncate parameter values for logging."""
        truncated = {}
        for key, value in params.items():
            if isinstance(value, str):
                truncated[key] = (
                    value[:max_length] + "..." if len(value) > max_length else value
                )
            elif isinstance(value, dict):
                truncated[key] = self._truncate_params(value, max_length)
            elif isinstance(value, list):
                # For lists, truncate each item
                truncated[key] = [
                    (
                        (
                            str(item)[:max_length] + "..."
                            if len(str(item)) > max_length
                            else item
                        )
                        if not isinstance(item, dict)
                        else self._truncate_params(item, max_length)
                    )
                    for item in value[:5]  # Only show first 5 items
                ]
                if len(value) > 5:
                    truncated[key].append(f"... and {len(value) - 5} more items")
            else:
                truncated[key] = value
        return truncated

    async def process_hook_event(self, hook_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process incoming hook event."""
        hook_type = hook_data.get("hook_event_name")
        hook_cwd = hook_data.get("cwd") or hook_data.get("current_working_directory")

        # Enhanced logging for all hook events
        logger.info(
            "Received hook event",
            hook_type=hook_type,
            hook_cwd=hook_cwd,
            target_cwd=self.target_cwd,
            data_keys=list(hook_data.keys()),
        )

        # Extra detailed logging for Notification hooks to understand permission patterns
        if hook_type == "Notification":
            message = hook_data.get("message", "")
            logger.info(
                "Notification hook details",
                session_id=hook_data.get("session_id", "unknown"),
                full_message=message,  # Full message without truncation
                message_length=len(message),
                transcript_path=hook_data.get("transcript_path", ""),
                timestamp=hook_data.get("timestamp", ""),
                all_hook_data=hook_data,  # Complete hook data for pattern analysis
            )

        # Check if we should process this hook based on CWD (if filtering is enabled)
        if self.config.filter_hooks_by_cwd and self.target_cwd and hook_cwd:
            # Normalize paths for comparison
            hook_cwd_normalized = os.path.normpath(os.path.expanduser(hook_cwd))
            target_cwd_normalized = os.path.normpath(
                os.path.expanduser(self.target_cwd)
            )

            # Note: Claude may use cd commands, so it will change CWD during execution.
            # Check if hook_cwd is the target_cwd or a subdirectory of it
            try:
                # Convert to Path objects for easier comparison
                hook_path = Path(hook_cwd_normalized).resolve()
                target_path = Path(target_cwd_normalized).resolve()

                # Check if hook_path is relative to target_path (is subdirectory or same)
                hook_path.relative_to(target_path)
            except ValueError:
                # relative_to raises ValueError if hook_path is not under target_path
                logger.info(
                    "Ignoring hook from different CWD",
                    hook_type=hook_type,
                    hook_cwd=hook_cwd_normalized,
                    target_cwd=target_cwd_normalized,
                )
                # Return continue=True so Claude continues working
                return {"status": "ok", "continue": True}

        if hook_type == "Stop":
            session_id = hook_data.get("session_id")
            transcript_path = hook_data.get("transcript_path")

            # Log all hook data with truncated values
            logger.info(
                "Processing Stop hook",
                session_id=session_id,
                transcript_path=transcript_path,
                all_params=self._truncate_params(hook_data),
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

            # Log all hook data with truncated values
            logger.info(
                "Processing UserPromptSubmit hook",
                session_id=session_id,
                prompt_length=len(prompt),
                prompt_preview=prompt[:200] + "..." if len(prompt) > 200 else prompt,
                all_params=self._truncate_params(hook_data),
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
            self._limit_dict_size(self.recent_tool_usage)

            # Store tool context for fallback when transcript parsing fails
            if tool_name in [
                "Edit",
                "MultiEdit",
                "Write",
                "Bash",
                "ExitPlanMode",
                "Read",
            ]:
                self.recent_tool_context[session_id] = {
                    "tool_use": tool_name,
                    "tool_input": tool_input,
                    "timestamp": time.time(),
                }
                self._limit_dict_size(self.recent_tool_context)
                logger.debug(
                    "Stored tool context for fallback",
                    session_id=session_id,
                    tool_name=tool_name,
                    context_keys=list(self.recent_tool_context[session_id].keys()),
                )

            # Log all hook data with truncated values - this is the most important one for debugging
            truncated_tool_input = (
                self._truncate_params(tool_input)
                if isinstance(tool_input, dict)
                else str(tool_input)[:200]
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
                tool_input_truncated=truncated_tool_input,
                all_params=self._truncate_params(hook_data),
            )

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

            # Log all hook data with truncated values
            truncated_tool_input = (
                self._truncate_params(tool_input)
                if isinstance(tool_input, dict)
                else str(tool_input)[:200]
            )
            truncated_tool_response = (
                self._truncate_params(tool_response)
                if isinstance(tool_response, dict)
                else str(tool_response)[:200]
            )

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
                tool_input_truncated=truncated_tool_input,
                tool_response_truncated=truncated_tool_response,
                all_params=self._truncate_params(hook_data),
            )

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

            # Detailed logging for permission dialog analysis (replaces truncated logging above)
            logger.info(
                "Processing Notification hook",
                session_id=session_id,
                full_message=message,  # Complete message for pattern analysis
                message_type=(
                    "permission_request"
                    if "permission" in message.lower()
                    else (
                        "idle_timeout"
                        if "waiting for your input" in message.lower()
                        else "unknown_notification"
                    )
                ),
                transcript_path=transcript_path,
                has_recent_tool_usage=session_id in self.recent_tool_usage,
                recent_tool_context=self.recent_tool_context.get(session_id, {}),
            )

            # Check if this is a permission dialog based on message content
            if self._is_permission_dialog(session_id, message):
                logger.info(
                    "PERMISSION_DIALOG_DETECTED",  # Make it easy to grep for these
                    session_id=session_id,
                    full_permission_message=message,
                    recent_tool_used=self.recent_tool_context.get(session_id, {}).get(
                        "tool_use", "unknown"
                    ),
                    tool_context_full=self.recent_tool_context.get(session_id, {}),
                    notification_hook_data=hook_data,  # Complete hook data
                    detection_method=(
                        "message_content"
                        if "permission" in message.lower()
                        else "recent_tool_timing"
                    ),
                )

                # Extract tool name from permission message (last word)
                permission_tool_name = self._extract_tool_name_from_permission_message(
                    message
                )

                if permission_tool_name:
                    # Try to find the most recent matching tool context
                    context = self._find_matching_tool_context(
                        session_id, permission_tool_name
                    )

                    if context:
                        logger.info(
                            "Found matching tool context for permission dialog",
                            session_id=session_id,
                            permission_tool=permission_tool_name,
                            actual_tool=context.get("tool_use"),
                            tool_input_keys=list(context.get("tool_input", {}).keys()),
                        )
                    else:
                        # Fallback to most recent context
                        recent_context = self.recent_tool_context.get(session_id, {})
                        if recent_context:
                            context = recent_context
                            logger.warning(
                                "No matching tool found for permission dialog, using most recent",
                                session_id=session_id,
                                permission_tool=permission_tool_name,
                                recent_tool=recent_context.get("tool_use"),
                            )
                        else:
                            logger.warning(
                                "No tool context available for permission dialog",
                                session_id=session_id,
                                permission_tool=permission_tool_name,
                            )
                            return {"continue": True}
                else:
                    # Can't parse tool name, use recent context
                    recent_context = self.recent_tool_context.get(session_id, {})
                    if recent_context:
                        context = recent_context
                        logger.warning(
                            "Could not parse tool from permission message, using recent context",
                            session_id=session_id,
                            tool_name=recent_context.get("tool_use"),
                        )
                    else:
                        logger.warning(
                            "No tool context available for permission dialog",
                            session_id=session_id,
                        )
                        return {"continue": True}

                # Read tmux pane to get actual permission options
                tmux_content = await self._read_tmux_pane_content()
                permission_options = self._parse_permission_options(tmux_content)

                # Add options to context
                context["permission_options"] = permission_options

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

        # Log any other hook types we might receive
        logger.info(
            f"Received unhandled hook type: {hook_type}",
            hook_type=hook_type,
            session_id=hook_data.get("session_id", "unknown"),
            all_params=self._truncate_params(hook_data),
        )

        # Still return continue=True for unknown hooks so Claude isn't blocked
        return {"status": "ok", "continue": True}

    def _is_permission_dialog(self, session_id: str, message: str = "") -> bool:
        """Check if a notification is a permission dialog based on message content and context.

        Permission dialogs occur when Claude needs permission after attempting to use a tool.
        Idle timeout notifications occur when there's no recent tool usage.
        """
        # First check message content for clear indicators
        message_lower = message.lower()

        # If message says "waiting for your input", it's NOT a permission dialog
        if "waiting for your input" in message_lower:
            logger.info(
                "Not a permission dialog - Claude is waiting for input",
                session_id=session_id,
                message=message,
            )
            return False

        # If message contains "permission", it IS a permission dialog
        if "permission" in message_lower:
            logger.info(
                "Permission dialog detected by message content",
                session_id=session_id,
                message=message,
            )
            return True

        # For other messages, fall back to timing check
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
        elif tool_name == "ExitPlanMode":
            # For ExitPlanMode, extract the plan content
            plan_content = tool_input.get("plan", "")
            context_info["plan"] = plan_content
            context_info["code_snippet"] = (
                plan_content  # Also store in code_snippet for compatibility
            )
        elif tool_name == "Read":
            # For Read, extract the file path
            context_info["file_path"] = tool_input.get("file_path", "")
            context_info["code_snippet"] = None
            context_info["new_code"] = None

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

    async def _read_tmux_pane_content(self) -> str:
        """Read current tmux pane content to extract permission options."""
        try:
            import subprocess

            # Get the target pane (same logic as tmux integration)
            if self.config.pane:
                target_pane = self.config.pane
            else:
                # Auto-discover Claude pane
                result = subprocess.run(
                    [
                        "tmux",
                        "list-panes",
                        "-a",
                        "-F",
                        "#{session_name}:#{window_index}.#{pane_index} #{pane_current_command}",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )

                if result.returncode != 0:
                    return ""

                # Find pane running claude (same logic as facade.py)
                for line in result.stdout.strip().split("\n"):
                    parts = line.split(" ", 1)
                    if len(parts) == 2 and parts[1] == "claude":
                        target_pane = parts[0]
                        break
                else:
                    return ""

            # Capture pane content (last 30 lines should be enough)
            result = subprocess.run(
                ["tmux", "capture-pane", "-t", target_pane, "-p", "-S", "-30"],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode == 0:
                content = result.stdout
                logger.info(
                    "Captured tmux pane content",
                    target_pane=target_pane,
                    content_length=len(content),
                    content_preview=content[-200:] if content else "",
                )
                return content
            else:
                logger.warning(
                    "Failed to capture tmux pane content",
                    returncode=result.returncode,
                    stderr=result.stderr,
                )
                return ""

        except Exception as e:
            logger.error("Error reading tmux pane content", error=str(e))
            return ""

    def _extract_tool_name_from_permission_message(self, message: str) -> Optional[str]:
        """Extract tool name from permission message.

        Format: "Claude needs your permission to use X" where X is the tool name.
        """
        if not message:
            return None

        # Split message and get the last word
        words = message.strip().split()
        if words:
            tool_name = words[-1]
            # Remove any punctuation
            tool_name = tool_name.rstrip(".,!?")
            return tool_name

        return None

    def _find_matching_tool_context(
        self, session_id: str, permission_tool_name: str
    ) -> Optional[Dict[str, Any]]:
        """Find the most recent tool context that matches the permission dialog.

        Maps permission tool names to actual tool names:
        - "Read" -> Read
        - "Update" -> Edit, MultiEdit, Write
        - "Fetch" -> WebFetch, Fetch
        """
        # Get the most recent tool context
        recent_context = self.recent_tool_context.get(session_id)
        if not recent_context:
            return None

        actual_tool_name = recent_context.get("tool_use")

        # Direct match
        if permission_tool_name == actual_tool_name:
            return recent_context

        # Handle Update -> file modification tools mapping
        if permission_tool_name == "Update" and actual_tool_name in [
            "Edit",
            "MultiEdit",
            "Write",
        ]:
            return recent_context

        # Handle Fetch -> web fetch tools mapping
        if permission_tool_name == "Fetch" and actual_tool_name in [
            "WebFetch",
            "Fetch",
        ]:
            return recent_context

        # No match found
        return None

    def _parse_permission_options(self, tmux_content: str) -> List[str]:
        """Parse numbered list of permission options from tmux pane content."""
        if not tmux_content:
            return []

        lines = tmux_content.strip().split("\n")
        options = []

        # Look for the last numbered list in the content
        # Pattern: lines starting with "❯ 1. " or "  2. " etc.
        current_list = []
        current_option_text = ""

        import re

        for line in lines:
            stripped = line.strip()

            # Skip lines that don't contain digits (optimization)
            if not any(c.isdigit() for c in stripped):
                continue

            # Check if line starts with optional "│" then optional "❯ " followed by number, dot and space
            # Handles: "│ ❯ 1. Yes" and "│   2. Yes, and don't ask again..."
            match = re.match(r"^│?\s*❯?\s*(\d+)\.\s+(.+)", stripped)

            if match:
                # If we were building a multi-line option, save it
                if current_option_text and current_list:
                    current_list[-1] = current_option_text.strip()

                number = int(match.group(1))
                text = match.group(2).strip()
                # Remove trailing box drawing characters and other unwanted chars
                text = re.sub(r"[│╰╯╭╮┌┐└┘├┤┬┴┼─━═║╔╗╚╝╠╣╦╩╬]*$", "", text).strip()

                # If this is number 1, start a new list
                if number == 1:
                    current_list = [text]
                    current_option_text = text
                # If this continues the sequence, add to current list
                elif number == len(current_list) + 1:
                    current_list.append(text)
                    current_option_text = text
                # If sequence is broken, start over
                else:
                    current_list = [text] if number == 1 else []
                    current_option_text = text if number == 1 else ""
            else:
                # Check if this line is a continuation of the previous option
                # (e.g., wrapped text from a long option)
                # Look for lines that start with "│   " (continuation) but not "│ ❯" or "│   N." (new options)
                continuation_match = re.match(r"^│\s+([^❯\d].+)", stripped)
                if current_list and current_option_text and continuation_match:
                    continuation_text = continuation_match.group(1).strip()
                    # Remove trailing box drawing characters
                    continuation_text = re.sub(
                        r"[│╰╯╭╮┌┐└┘├┤┬┴┼─━═║╔╗╚╝╠╣╦╩╬]*$", "", continuation_text
                    ).strip()
                    current_option_text += " " + continuation_text
                else:
                    # If we were building a multi-line option, save it
                    if current_option_text and current_list:
                        current_list[-1] = current_option_text.strip()
                        current_option_text = ""

        # Save any remaining multi-line option
        if current_option_text and current_list:
            current_list[-1] = current_option_text.strip()

        # Use the last complete numbered list we found
        if current_list:
            options = current_list

        logger.info(
            "Parsed permission options from tmux",
            options_count=len(options),
            options=options,
            tmux_content_preview=tmux_content[-500:] if tmux_content else "",
            full_tmux_content=(
                tmux_content[:1000] if tmux_content else ""
            ),  # Show first 1000 chars for debugging
        )

        return options

    def _limit_dict_size(self, target_dict: Dict, max_size: int = 1000) -> None:
        """Remove oldest entries if dict exceeds max_size."""
        if len(target_dict) <= max_size:
            return

        # Get items with timestamps, fallback to arbitrary order for dicts without timestamps
        items = list(target_dict.items())

        # Try to sort by timestamp if available
        try:
            if items and isinstance(items[0][1], dict) and "timestamp" in items[0][1]:
                # For tool_context dict with timestamp in the value
                items.sort(key=lambda x: x[1].get("timestamp", 0))
            elif items and isinstance(items[0][1], (int, float)):
                # For tool_usage dict where value is the timestamp
                items.sort(key=lambda x: x[1])
            else:
                # For dicts without timestamps, just remove from the beginning
                pass
        except (IndexError, KeyError, TypeError):
            pass

        # Remove oldest entries to get back to max_size
        entries_to_remove = len(target_dict) - max_size
        for i in range(entries_to_remove):
            key_to_remove = items[i][0]
            del target_dict[key_to_remove]

    async def stop(self):
        """Stop the server and cleanup."""
        if self.server:
            self.server.close()
            await self.server.wait_closed()

        # Remove socket file
        if self.socket_path.exists():
            self.socket_path.unlink()
