"""Minimal Claude Code integration facade."""

from pathlib import Path
from typing import Callable, Optional

import structlog

from ..config.settings import Settings
from .responses import ClaudeResponse, StreamUpdate
from .tmux_integration import TmuxClaudeIntegration

logger = structlog.get_logger()


class ClaudeIntegration:
    """Minimal Claude integration via tmux relay."""

    def __init__(
        self,
        config: Settings,
        tmux_integration: Optional[TmuxClaudeIntegration] = None,
    ):
        """Initialize minimal Claude integration."""
        self.config = config
        self.tmux_integration = tmux_integration
        self._pane_target = None

    async def _get_pane_target(self) -> str:
        """Get tmux pane target, using auto-discovery if not configured."""
        if self._pane_target:
            return self._pane_target
            
        if self.config.tmux_pane and self.config.tmux_pane.strip():
            self._pane_target = self.config.tmux_pane.strip()
            logger.info("Using configured tmux pane", pane=self._pane_target)
            return self._pane_target
        
        from src.tmux.client import TmuxClient
        self._pane_target = await TmuxClient.discover_claude_pane()
        logger.info("Auto-discovered claude pane", pane=self._pane_target)
        return self._pane_target

    async def _ensure_tmux_integration(self) -> None:
        """Ensure tmux integration is initialized with correct pane."""
        if not self.tmux_integration:
            from src.tmux.client import TmuxClient
            pane_target = await self._get_pane_target()
            tmux_client = TmuxClient(pane_target)
            self.tmux_integration = TmuxClaudeIntegration(self.config, tmux_client)
        
        self.manager = self.tmux_integration

    async def run_command(
        self,
        prompt: str,
        working_directory: Optional[Path] = None,
        user_id: int = 0,
        on_stream: Optional[Callable[[StreamUpdate], None]] = None,
    ) -> ClaudeResponse:
        """Run Claude Code command via tmux relay."""
        logger.info("Relaying command to Claude via tmux", user_id=user_id)

        # Ensure tmux integration is set up
        await self._ensure_tmux_integration()

        working_dir = working_directory or Path.cwd()
        
        return await self._execute_with_fallback(
            prompt=prompt,
            working_directory=working_dir,
            session_id=f"user_{user_id}",
        )

    async def _execute_with_fallback(
        self,
        prompt: str,
        working_directory: Path,
        session_id: Optional[str] = None,
        continue_session: bool = False,
        stream_callback: Optional[Callable] = None,
    ) -> ClaudeResponse:
        """Execute command via tmux only."""
        logger.debug("Executing via tmux")
        return await self.tmux_integration.execute_command(
            prompt=prompt,
            working_directory=working_directory,
            session_id=session_id,
            continue_session=continue_session,
            stream_callback=stream_callback,
        )

    async def shutdown(self) -> None:
        """Shutdown integration and cleanup resources."""
        logger.info("Shutting down Claude integration")
