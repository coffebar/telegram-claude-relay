"""Proactive permission dialog monitoring for faster response times."""

import asyncio
import hashlib
import json
import sys
import time

from typing import Any, Dict, List, Optional

import structlog


# TaskGroup compatibility for Python < 3.11
if sys.version_info >= (3, 11):
    from asyncio import TaskGroup
else:
    # Fallback implementation for older Python versions
    class TaskGroup:
        def __init__(self):
            self._tasks = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            # Cancel all tasks
            for task in self._tasks:
                if not task.done():
                    task.cancel()

            # Wait for all tasks to complete
            if self._tasks:
                await asyncio.gather(*self._tasks, return_exceptions=True)

            return False

        def create_task(self, coro):
            task = asyncio.create_task(coro)
            self._tasks.append(task)
            return task


logger = structlog.get_logger()

# Configuration constants
MONITORING_DURATION = 5.0  # 5 seconds maximum monitoring window
MONITORING_INTERVAL = 0.5  # 500ms between checks
CLEANUP_INTERVAL = 60.0  # 60 seconds between cleanup runs


class PermissionMonitor:
    """Singleton monitor for proactive permission dialog detection.

    Coordinates with existing unix socket server to monitor for permission dialogs
    and send quick previews when detected.
    """

    _instance: Optional["PermissionMonitor"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._initialized = True
        self.task_group: Optional[TaskGroup] = None
        self.monitoring_tasks: Dict[str, asyncio.Task] = {}  # Keep for session tracking
        self.unix_socket_server = None  # Reference to server for tmux access
        self.conversation_monitor = (
            None  # Reference to conversation monitor for sending messages
        )

        # Track simplified messages with context matching
        self.simplified_dialogs: Dict[str, Dict[str, Any]] = (
            {}
        )  # session_id -> {tool_context, timestamp, user_responded}

        # Periodic cleanup task
        self.cleanup_task: Optional[asyncio.Task] = None

        logger.info("PermissionMonitor singleton initialized")

    async def configure(self, unix_socket_server, conversation_monitor):
        """Configure the monitor with required dependencies."""
        self.unix_socket_server = unix_socket_server
        self.conversation_monitor = conversation_monitor

        # Initialize task group for automatic cleanup
        self.task_group = TaskGroup()
        await self.task_group.__aenter__()

        # Start periodic cleanup task
        self._start_cleanup_task()

        logger.info("PermissionMonitor configured with dependencies")

    def _start_cleanup_task(self):
        """Start the periodic cleanup task."""
        if self.cleanup_task is None or self.cleanup_task.done():
            if self.task_group:
                self.cleanup_task = self.task_group.create_task(
                    self._run_periodic_cleanup()
                )
            else:
                self.cleanup_task = asyncio.create_task(self._run_periodic_cleanup())
            logger.info("Started periodic cleanup task")

    async def _run_periodic_cleanup(self):
        """Run periodic cleanup every CLEANUP_INTERVAL seconds."""
        try:
            while True:
                await asyncio.sleep(CLEANUP_INTERVAL)
                self.cleanup_old_sessions()
        except asyncio.CancelledError:
            logger.info("Periodic cleanup task cancelled")
            raise
        except Exception as e:
            logger.error("Error in periodic cleanup task", error=str(e), exc_info=True)

    async def shutdown(self):
        """Shutdown the permission monitor and clean up resources."""
        try:
            # Shutdown task group (automatically cancels all tasks)
            if self.task_group:
                await self.task_group.__aexit__(None, None, None)
                self.task_group = None

            # Clear task references
            self.monitoring_tasks.clear()
            self.cleanup_task = None

            # Final cleanup
            self.cleanup_old_sessions()

            logger.info("PermissionMonitor shutdown completed")

        except Exception as e:
            logger.error("Error during shutdown", error=str(e), exc_info=True)

    def _create_permission_context_hash(self, context: Dict[str, Any]) -> str:
        """Create a unique hash for permission context comparison.

        This hash uniquely identifies a specific tool use request to prevent
        duplicate permission dialogs for the same action.

        Args:
            context: Tool context containing tool_use and tool_input

        Returns:
            A hash string uniquely identifying this permission request
        """
        try:
            tool_name = context.get("tool_use", "")
            tool_input = context.get("tool_input", {})

            # Create a deterministic string representation
            # Include both tool name and input for uniqueness
            context_data = {"tool": tool_name, "input": tool_input}

            # Sort keys for consistent hashing
            context_str = json.dumps(context_data, sort_keys=True)

            # Create hash (using SHA256 for better collision resistance)
            hash_obj = hashlib.sha256(context_str.encode())
            return hash_obj.hexdigest()

        except Exception as e:
            logger.error(
                "Failed to create context hash",
                error=str(e),
                context_keys=list(context.keys()) if context else None,
            )
            # Return a unique fallback to avoid blocking
            return f"error_{time.time()}"

    async def start_monitoring(
        self, session_id: str, tool_context: Dict[str, Any]
    ) -> None:
        """Start monitoring for permission dialog after PreToolUse hook.

        Args:
            session_id: The Claude session ID
            tool_context: Context from PreToolUse hook including tool name and parameters
        """
        try:
            # Cancel any existing monitoring for this session (timer reset for latest tool)
            await self.stop_monitoring(session_id)

            # Reset simplified message tracking for new monitoring
            # (handled by clearing simplified_dialogs below)

            # Clear any previous simplified dialog tracking for fresh start
            if session_id in self.simplified_dialogs:
                del self.simplified_dialogs[session_id]

            # Create new monitoring task using TaskGroup
            if self.task_group:
                task = self.task_group.create_task(
                    self._monitor_session(session_id, tool_context)
                )
            else:
                task = asyncio.create_task(
                    self._monitor_session(session_id, tool_context)
                )
            self.monitoring_tasks[session_id] = task

            logger.info(
                "Started permission monitoring",
                session_id=session_id,
                tool_name=tool_context.get("tool_use"),
                active_monitors=len(self.monitoring_tasks),
            )

        except Exception as e:
            logger.error(
                "Failed to start monitoring",
                session_id=session_id,
                error=str(e),
                exc_info=True,
            )

    async def stop_monitoring(self, session_id: str) -> None:
        """Stop monitoring for a specific session.

        Args:
            session_id: The Claude session ID to stop monitoring
        """
        task = self.monitoring_tasks.get(session_id)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        if session_id in self.monitoring_tasks:
            del self.monitoring_tasks[session_id]
            logger.debug(
                "Stopped monitoring",
                session_id=session_id,
                remaining_monitors=len(self.monitoring_tasks),
            )

    async def _monitor_session(
        self, session_id: str, tool_context: Dict[str, Any]
    ) -> None:
        """Monitor tmux pane for permission dialog patterns.

        This runs in background for up to 5 seconds, checking every 500ms.
        If permission options are detected, sends simplified message immediately.
        """
        start_time = time.time()
        check_count = 0

        try:
            while time.time() - start_time < MONITORING_DURATION:
                check_count += 1

                # Read current tmux content and check for permission dialog patterns
                tmux_content = await self._read_tmux_content()
                if tmux_content:
                    permission_options = self._parse_permission_options(tmux_content)

                    if permission_options and session_id not in self.simplified_dialogs:
                        logger.info(
                            "Permission dialog detected during monitoring",
                            session_id=session_id,
                            options_count=len(permission_options),
                            elapsed_time=time.time() - start_time,
                            check_number=check_count,
                        )

                        # Send simplified permission message immediately
                        await self._send_simplified_permission(
                            session_id=session_id,
                            tool_context=tool_context,
                            options=permission_options,
                        )

                        # Track this simplified dialog with hash for later matching
                        context_hash = self._create_permission_context_hash(
                            tool_context
                        )
                        # Store minimal data to prevent memory accumulation
                        self.simplified_dialogs[session_id] = {
                            "context_hash": context_hash,
                            "timestamp": time.time(),
                            "user_responded": False,
                            "tool_name": tool_context.get("tool_use", "Unknown"),
                        }
                        # Session is now tracked in simplified_dialogs dictionary

                        logger.info(
                            "Stored permission context hash (simplified)",
                            session_id=session_id,
                            tool_name=tool_context.get("tool_use", "Unknown"),
                            context_hash=context_hash[:16],  # Log first 16 chars
                            full_tool_context=tool_context,  # Log full context for debugging
                        )

                        # Stop monitoring timer to save resources
                        logger.info(
                            "Stopping monitoring after successful dialog detection",
                            session_id=session_id,
                            total_checks=check_count,
                        )
                        # Clean up monitoring task immediately
                        if session_id in self.monitoring_tasks:
                            del self.monitoring_tasks[session_id]
                        return

                await asyncio.sleep(MONITORING_INTERVAL)

        except asyncio.CancelledError:
            logger.debug(
                "Monitoring cancelled",
                session_id=session_id,
                elapsed_time=time.time() - start_time,
                checks_performed=check_count,
            )
            raise

        except Exception as e:
            logger.error(
                "Error during monitoring",
                session_id=session_id,
                error=str(e),
                exc_info=True,
                elapsed_time=time.time() - start_time,
                checks_performed=check_count,
            )

        finally:
            # Clean up monitoring task
            if session_id in self.monitoring_tasks:
                del self.monitoring_tasks[session_id]

            logger.debug(
                "Monitoring completed",
                session_id=session_id,
                total_time=time.time() - start_time,
                total_checks=check_count,
                found_dialog=session_id in self.simplified_dialogs,
            )

    async def _read_tmux_content(self) -> str:
        """Read current tmux pane content using unix socket server's method."""
        try:
            if not self.unix_socket_server:
                logger.warning("No unix socket server configured")
                return ""

            # Use existing method from unix socket server
            content = await self.unix_socket_server._read_tmux_pane_content()
            return content

        except Exception as e:
            logger.error("Failed to read tmux content", error=str(e), exc_info=True)
            return ""

    def _parse_permission_options(self, content: str) -> List[str]:
        """Parse permission options using existing server method."""
        try:
            if not self.unix_socket_server:
                logger.warning("No unix socket server configured")
                return []

            # Use existing enhanced parsing method from unix socket server
            options = self.unix_socket_server._parse_permission_options(content)
            return options

        except Exception as e:
            logger.error(
                "Failed to parse permission options", error=str(e), exc_info=True
            )
            return []

    async def _send_simplified_permission(
        self, session_id: str, tool_context: Dict[str, Any], options: List[str]
    ) -> None:
        """Send simplified permission message with just tool name and buttons."""
        try:
            if not self.conversation_monitor:
                logger.warning("No conversation monitor configured")
                return

            tool_name = tool_context.get("tool_use", "Unknown")

            # Create simplified permission dialog data for immediate sending
            dialog_data = {
                "session_id": session_id,
                "message": f"⚡ Claude needs permission to use {tool_name}",
                "context": {
                    **tool_context,
                    "permission_options": options,
                    "is_simplified_preview": True,  # Mark as quick preview
                },
                "timestamp": time.time(),
            }

            # Send via existing conversation monitor permission dialog method
            await self.conversation_monitor.send_permission_dialog(dialog_data)

            logger.info(
                "Sent simplified permission dialog",
                session_id=session_id,
                tool_name=tool_name,
                options_count=len(options),
            )

        except Exception as e:
            logger.error(
                "Failed to send simplified permission",
                session_id=session_id,
                error=str(e),
                exc_info=True,
            )

    async def send_full_permission_dialog(
        self, session_id: str, full_message: str, full_context: Dict[str, Any]
    ) -> bool:
        """Send full permission dialog when Notification hook arrives.

        Skips only if:
        1. Simplified message was sent for this exact tool context
        2. User already responded to the simplified dialog

        Returns:
            True if dialog was sent, False if skipped (duplicate) or failed
        """
        try:
            if not self.conversation_monitor:
                logger.warning("No conversation monitor configured")
                return False

            simplified_info = self.simplified_dialogs.get(session_id)

            # Cancel any active monitoring for this session (Notification arrived)
            await self.stop_monitoring(session_id)

            # Check if we should skip the full dialog
            should_skip = False
            if simplified_info:
                # Compare context hashes to detect exact same permission request
                current_hash = self._create_permission_context_hash(full_context)
                simplified_hash = simplified_info.get("context_hash", "")
                user_responded = simplified_info.get("user_responded", False)

                # Log for debugging
                logger.info(
                    "Comparing permission contexts",
                    session_id=session_id,
                    current_hash=current_hash[
                        :16
                    ],  # Log only first 16 chars for brevity
                    simplified_hash=simplified_hash[:16] if simplified_hash else "none",
                    hashes_match=current_hash == simplified_hash,
                    user_responded=user_responded,
                    full_context_for_hash=full_context,  # Log full context for debugging
                )

                if current_hash == simplified_hash:
                    # Exact same permission request
                    should_skip = True
                    logger.info(
                        "Skipping duplicate permission dialog - already sent for this exact context",
                        session_id=session_id,
                        tool_name=full_context.get("tool_use", "Unknown"),
                        context_hash=current_hash[:16],
                    )
                else:
                    # Different context (even if same tool)
                    logger.info(
                        "Sending full permission dialog - different tool context",
                        session_id=session_id,
                        current_tool=full_context.get("tool_use", "Unknown"),
                        simplified_tool=simplified_info.get("tool_name", "Unknown"),
                        hash_mismatch=True,
                    )

            if should_skip:
                # Clean up tracking data for duplicate
                if session_id in self.simplified_dialogs:
                    del self.simplified_dialogs[session_id]
                return False  # Dialog was skipped (duplicate)

            # Send full permission dialog using existing conversation monitor method
            dialog_data = {
                "session_id": session_id,
                "message": full_message,
                "context": full_context,
                "timestamp": time.time(),
            }

            await self.conversation_monitor.send_permission_dialog(dialog_data)

            # Clean up tracking data after successful send
            if session_id in self.simplified_dialogs:
                del self.simplified_dialogs[session_id]

            logger.info(
                "Sent full permission dialog",
                session_id=session_id,
                full_message_length=len(full_message),
                had_simplified=simplified_info is not None,
            )
            return True  # Dialog was sent

        except Exception as e:
            logger.error(
                "Failed to send full permission dialog",
                session_id=session_id,
                error=str(e),
                exc_info=True,
            )
            return False  # Failed to send

    def mark_user_responded(self, session_id: str) -> None:
        """Mark that user responded to a simplified permission dialog."""
        if session_id in self.simplified_dialogs:
            self.simplified_dialogs[session_id]["user_responded"] = True
            logger.info(
                "Marked user response for simplified dialog",
                session_id=session_id,
                tool_name=self.simplified_dialogs[session_id].get("tool_name"),
            )

    def cleanup_old_sessions(self, max_age_seconds: int = 300) -> None:
        """Clean up old monitoring data to prevent memory leaks."""
        try:
            current_time = time.time()

            # Cancel old monitoring tasks
            for session_id in list(self.monitoring_tasks.keys()):
                task = self.monitoring_tasks[session_id]
                if task.done():
                    del self.monitoring_tasks[session_id]

            # Clean up old simplified dialog tracking
            expired_sessions = [
                session_id
                for session_id, info in self.simplified_dialogs.items()
                if current_time - info.get("timestamp", 0) > max_age_seconds
            ]

            for session_id in expired_sessions:
                del self.simplified_dialogs[session_id]
                logger.debug(
                    "Cleaned up expired simplified dialog", session_id=session_id
                )

            logger.debug(
                "Cleanup completed",
                active_monitors=len(self.monitoring_tasks),
                simplified_dialogs=len(self.simplified_dialogs),
                expired_cleaned=len(expired_sessions),
            )

        except Exception as e:
            logger.error("Error during cleanup", error=str(e), exc_info=True)

    async def handle_notification_hook(
        self, session_id: str, message: str, context: Dict[str, Any]
    ) -> bool:
        """Handle Notification hook - stop monitoring and update message if needed.

        Args:
            session_id: The Claude session ID
            message: The notification message
            context: Full context from the hook

        Returns:
            True if permission dialog was handled (sent or skipped duplicate),
            False if unix socket server should continue processing
        """
        try:
            # Stop any active monitoring for this session
            await self.stop_monitoring(session_id)

            # Check if we have a simplified message - compare hashes and decide
            if session_id in self.simplified_dialogs:
                await self.send_full_permission_dialog(
                    session_id=session_id, full_message=message, full_context=context
                )
                # Always return True when simplified dialog exists - we "handled" it
                # (either by sending new dialog or by skipping duplicate)
                return True

            # No simplified dialog exists - let unix socket server handle it
            return False

        except Exception as e:
            logger.error(
                "Error handling notification hook",
                session_id=session_id,
                error=str(e),
                exc_info=True,
            )
            return False


# Global singleton instance
permission_monitor = PermissionMonitor()
