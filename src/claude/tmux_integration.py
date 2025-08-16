"""tmux-based Claude integration for sending commands (responses via hooks)."""

import time

from typing import Callable, Optional

import structlog

from src.config.settings import Settings
from src.tmux.client import TmuxClient

from .responses import ClaudeResponse, StreamUpdate


logger = structlog.get_logger()


class ClaudeTmuxIntegration:
    """Claude integration for sending commands via tmux (responses delivered via hooks)."""

    def __init__(self, config: Settings, tmux_client: TmuxClient):
        """Initialize tmux Claude integration.

        Args:
            config: Application settings
            tmux_client: tmux client for pane communication
        """
        self.config = config
        self.tmux_client = tmux_client
        self.last_full_output = ""

    async def execute_command(
        self,
        prompt: str,
        session_id: Optional[str] = None,
        stream_callback: Optional[Callable] = None,
    ) -> ClaudeResponse:
        """Send command via tmux pane (response delivered via hooks).

        Args:
            prompt: User prompt to send to Claude
            session_id: Session ID for tracking
            stream_callback: Optional callback for streaming updates (unused in hook mode)

        Returns:
            ClaudeResponse indicating command was sent successfully
        """
        start_time = time.time()

        try:
            logger.info(
                "Executing tmux command",
                pane=self.tmux_client.pane_target,
                prompt_length=len(prompt),
                session_id=session_id,
            )

            # Send prompt to Claude (response will be delivered via hooks)
            await self.send_prompt(prompt, stream_callback)

            # Calculate metrics
            duration_ms = int((time.time() - start_time) * 1000)

            logger.info(
                "tmux command sent successfully - response will be delivered via hooks",
                session_id=session_id,
                duration_ms=duration_ms,
            )

            # Create response indicating command was sent
            return ClaudeResponse(
                content="Command sent - response will be delivered via hooks",
                session_id=session_id or "tmux-session",
                cost=0.0,
                duration_ms=duration_ms,
                num_turns=1,
                is_error=False,
                tools_used=[],
            )

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            logger.error("tmux command execution failed", error=str(e))

            return ClaudeResponse(
                content=f"Error executing command via tmux: {e}",
                session_id=session_id or "tmux-session",
                cost=0.0,
                duration_ms=duration_ms,
                num_turns=1,
                is_error=True,
                error_type=type(e).__name__,
            )

    async def send_prompt(
        self, prompt: str, stream_callback: Optional[Callable] = None
    ) -> None:
        """Send prompt to Claude in tmux.

        Args:
            prompt: User prompt to send
            stream_callback: Optional callback for streaming updates
        """
        if stream_callback:
            await stream_callback(
                StreamUpdate(
                    type="system",
                    content=f"Sending prompt to tmux pane {self.tmux_client.pane_target}...",
                )
            )

        # Send the prompt
        await self.tmux_client.send_command(prompt)

        if stream_callback:
            await stream_callback(StreamUpdate(type="user", content=prompt))

    async def validate_setup(self) -> bool:
        """Validate tmux pane configuration.

        Returns:
            True if setup is valid, False otherwise
        """
        try:
            return await self.tmux_client.is_pane_active()
        except Exception as e:
            logger.error("tmux setup validation failed", error=str(e))
            return False

    async def get_status(self) -> dict:
        """Get tmux integration status.

        Returns:
            Status dictionary with pane information
        """
        try:
            pane_info = await self.tmux_client.get_pane_info()
            is_active = await self.tmux_client.is_pane_active()

            return {
                "type": "tmux",
                "active": is_active,
                "pane": self.tmux_client.pane_target,
                "info": pane_info,
            }
        except Exception as e:
            return {
                "type": "tmux",
                "active": False,
                "pane": self.tmux_client.pane_target,
                "error": str(e),
            }
