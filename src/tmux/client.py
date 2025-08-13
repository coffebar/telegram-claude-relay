"""tmux client for pane communication."""

import asyncio
import time

from typing import Dict, List

from src.tmux.exceptions import TmuxCommandError, TmuxPaneNotFoundError


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
        """Auto-discover first pane running 'claude' application.

        Returns:
            Pane target in format "session:window.pane"

        Raises:
            TmuxPaneNotFoundError: If no claude pane found
        """
        from src.tmux.exceptions import TmuxPaneNotFoundError

        # First try: Check pane_current_command directly
        cmd = [
            "tmux",
            "list-panes",
            "-a",
            "-F",
            "#{session_name}:#{window_index}.#{pane_index} #{pane_current_command} #{pane_pid}",
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
            for line in output.split("\n"):
                parts = line.split(" ", 2)
                if len(parts) >= 2:
                    pane_target = parts[0]
                    pane_cmd = parts[1]

                    # Direct match
                    if pane_cmd == "claude":
                        return pane_target

                    # Check process tree for claude child process
                    if len(parts) == 3:
                        pane_pid = parts[2]
                        if await TmuxClient._has_claude_child_process(pane_pid):
                            return pane_target

            raise TmuxPaneNotFoundError("No pane running 'claude' application found")

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
