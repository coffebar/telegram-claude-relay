"""tmux client for pane communication."""

import asyncio
import time

from typing import Dict, List

import structlog

from src.tmux.exceptions import TmuxCommandError, TmuxPaneNotFoundError


logger = structlog.get_logger()


class TmuxClient:
    """Client for communicating with tmux panes."""

    def __init__(self, pane_target: str):
        """Initialize tmux client.

        Args:
            pane_target: tmux pane target in format "session:window.pane"
        """
        self.pane_target = pane_target

    @staticmethod
    async def discover_claude_pane() -> str:
        """Auto-discover first available pane running 'claude' application.

        Returns:
            Pane target in format "session:window.pane"

        Raises:
            TmuxPaneNotFoundError: If no available claude pane found
        """
        from src.tmux.exceptions import TmuxPaneNotFoundError

        # First try: Check pane_current_command directly
        cmd = [
            "tmux",
            "list-panes",
            "-a",
            "-F",
            "#{session_name}:#{window_index}.#{pane_index} #{pane_current_command} #{pane_pid} #{pane_current_path}",
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                error_msg = stderr.decode().strip()
                raise TmuxCommandError(f"tmux command failed: {error_msg}")

            output = stdout.decode().strip()
            available_panes = []

            for line in output.split("\n"):
                parts = line.split(" ", 3)
                if len(parts) >= 3:
                    pane_target = parts[0]
                    pane_cmd = parts[1]
                    pane_pid = parts[2]
                    pane_cwd = parts[3] if len(parts) >= 4 else ""

                    # Check if this is a claude pane
                    is_claude_pane = False

                    # Direct match
                    if pane_cmd == "claude":
                        is_claude_pane = True
                    # Check process tree for claude child process
                    elif await TmuxClient._has_claude_child_process(pane_pid):
                        is_claude_pane = True

                    if is_claude_pane:
                        # Check if this pane is already claimed by checking for socket file
                        if await TmuxClient._is_pane_available(pane_cwd):
                            available_panes.append(pane_target)

            if available_panes:
                return available_panes[0]

            raise TmuxPaneNotFoundError(
                "No available pane running 'claude' application found"
            )

        except FileNotFoundError as e:
            raise TmuxCommandError("tmux command not found. Is tmux installed?") from e
        except Exception as e:
            raise TmuxCommandError(f"Failed to discover claude pane: {e}") from e

    @staticmethod
    async def _has_claude_child_process(pid: str) -> bool:
        """Check if a process has a claude child process.

        Args:
            pid: Process ID to check

        Returns:
            True if claude is found in the process tree
        """
        try:
            # Use pgrep to find claude processes with this parent PID
            cmd = ["pgrep", "-P", pid, "-x", "claude"]
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()

            # If pgrep finds any matches, it returns 0
            if proc.returncode == 0 and stdout.strip():
                return True

            # Also check grandchildren (claude might be a grandchild)
            # Get all children first
            cmd = ["pgrep", "-P", pid]
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()

            if proc.returncode == 0 and stdout.strip():
                child_pids = stdout.decode().strip().split("\n")
                for child_pid in child_pids:
                    # Check each child for claude grandchildren
                    cmd = ["pgrep", "-P", child_pid, "-x", "claude"]
                    proc = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    stdout, _ = await proc.communicate()
                    if proc.returncode == 0 and stdout.strip():
                        return True

        except Exception:
            # If pgrep fails, fall back to ps-based check
            try:
                cmd = ["ps", "-p", pid, "-o", "comm="]
                proc = await asyncio.create_subprocess_exec(
                    *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                stdout, _ = await proc.communicate()
                if proc.returncode == 0:
                    # Check if this process itself is claude
                    process_name = stdout.decode().strip()
                    if "claude" in process_name.lower():
                        return True
            except Exception:
                pass

        return False

    @staticmethod
    async def _is_socket_in_use(socket_path: str) -> bool:
        """Check if a Unix socket is actively being used by a process.

        Args:
            socket_path: Path to the Unix socket file

        Returns:
            True if socket is actively in use, False otherwise
        """
        try:
            # Use lsof to check if any process is using the socket
            proc = await asyncio.create_subprocess_exec(
                "lsof",
                socket_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            # lsof returns 0 if the file is in use, 1 if not
            return proc.returncode == 0 and stdout.strip()

        except (FileNotFoundError, Exception):
            # If lsof is not available or fails, assume socket is not in use
            return False

    @staticmethod
    async def _is_pane_available(pane_cwd: str) -> bool:
        """Check if a pane is available by checking for active socket files.

        Args:
            pane_cwd: Current working directory of the pane

        Returns:
            True if pane is available (no active socket exists), False if already claimed
        """
        if not pane_cwd or not pane_cwd.strip():
            return True  # If we can't determine CWD, assume available

        try:
            from pathlib import Path

            cwd_path = Path(pane_cwd.strip())
            if not cwd_path.exists():
                return True  # Directory doesn't exist, assume available

            # Extract project name from the path (last directory component)
            project_name = cwd_path.name
            if not project_name:
                return True  # Can't determine project name, assume available

            # Check for existing socket file in current working directory (where bot runs)
            # Socket files are created in the bot's CWD, not the pane's CWD
            from src.config.settings import Settings

            current_cwd = Path.cwd()
            socket_filename = Settings.generate_socket_path(project_name)
            socket_file = current_cwd / socket_filename

            # If socket file doesn't exist, pane is available
            if not socket_file.exists():
                return True

            # If socket file exists, check if it's actively in use
            is_active = await TmuxClient._is_socket_in_use(str(socket_file))

            # Pane is available only if socket exists but is not actively used
            return not is_active

        except Exception:
            # If any error occurs, assume pane is available to avoid blocking
            return True

    async def _run_tmux_command(self, args: List[str]) -> str:
        """Execute tmux command and return output.

        Args:
            args: tmux command arguments

        Returns:
            Command output as string

        Raises:
            TmuxCommandError: If tmux command fails
        """
        cmd = ["tmux"] + args

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                error_msg = stderr.decode().strip()
                raise TmuxCommandError(f"tmux command failed: {error_msg}")

            return stdout.decode().strip()
        except FileNotFoundError as e:
            raise TmuxCommandError("tmux command not found. Is tmux installed?") from e
        except Exception as e:
            raise TmuxCommandError(f"Failed to execute tmux command: {e}") from e

    async def send_command(self, text: str) -> None:
        """Send text to tmux pane.

        Args:
            text: Text to send to the pane

        Raises:
            TmuxCommandError: If sending fails
        """
        await self._run_tmux_command(["send-keys", "-t", self.pane_target, text])

        await asyncio.sleep(0.2)

        # Send Enter to submit
        await self._run_tmux_command(["send-keys", "-t", self.pane_target, "Enter"])

    async def send_escape_key(self) -> None:
        """Send Escape key to tmux pane.

        Raises:
            TmuxCommandError: If sending fails
        """
        # Send the Escape key directly without Enter
        await self._run_tmux_command(["send-keys", "-t", self.pane_target, "Escape"])

    async def capture_output(self, lines: int = 100) -> str:
        """Capture recent pane content.

        Args:
            lines: Number of lines to capture

        Returns:
            Captured pane content

        Raises:
            TmuxCommandError: If capture fails
        """
        try:
            return await self._run_tmux_command(
                ["capture-pane", "-t", self.pane_target, "-S", f"-{lines}", "-p"]
            )
        except TmuxCommandError as e:
            if "can't find pane" in str(e).lower():
                raise TmuxPaneNotFoundError(
                    f"Pane not found: {self.pane_target}"
                ) from e
            raise

    async def is_pane_active(self) -> bool:
        """Check if pane exists and is accessible.

        Returns:
            True if pane is active, False otherwise
        """
        try:
            await self._run_tmux_command(
                ["display-message", "-t", self.pane_target, "-p", "#{pane_id}"]
            )
            return True
        except TmuxCommandError:
            return False

    async def get_pane_info(self) -> Dict[str, str]:
        """Get information about the target pane.

        Returns:
            Dictionary with pane information

        Raises:
            TmuxPaneNotFoundError: If pane is not found
        """
        try:
            info = await self._run_tmux_command(
                [
                    "display-message",
                    "-t",
                    self.pane_target,
                    "-p",
                    "#{session_name}:#{window_index}.#{pane_index} #{pane_current_command}",
                ]
            )
            return {"pane": info}
        except TmuxCommandError as e:
            if "can't find pane" in str(e).lower():
                raise TmuxPaneNotFoundError(
                    f"Pane not found: {self.pane_target}"
                ) from e
            raise

    async def get_pane_cwd(self) -> str:
        """Get the current working directory of the target pane.

        Returns:
            Current working directory path

        Raises:
            TmuxPaneNotFoundError: If pane is not found
        """
        try:
            cwd = await self._run_tmux_command(
                [
                    "display-message",
                    "-t",
                    self.pane_target,
                    "-p",
                    "#{pane_current_path}",
                ]
            )
            return cwd.strip()
        except TmuxCommandError as e:
            if "can't find pane" in str(e).lower():
                raise TmuxPaneNotFoundError(
                    f"Pane not found: {self.pane_target}"
                ) from e
            raise

    async def wait_for_output_change(
        self, initial_output: str, timeout: int = 30, poll_interval: float = 1.0
    ) -> str:
        """Wait for output to change from initial state.

        Args:
            initial_output: Initial pane content to compare against
            timeout: Maximum time to wait in seconds
            poll_interval: Time between checks in seconds

        Returns:
            New output content

        Raises:
            TmuxResponseTimeoutError: If no change detected within timeout
        """
        from src.tmux.exceptions import TmuxResponseTimeoutError

        start_time = time.time()

        while time.time() - start_time < timeout:
            current_output = await self.capture_output()

            # Check if output changed
            if current_output != initial_output:
                return current_output

            await asyncio.sleep(poll_interval)

        raise TmuxResponseTimeoutError(
            f"No output change detected within {timeout} seconds"
        )
