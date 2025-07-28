"""tmux-based Claude integration."""

import asyncio
import time
from pathlib import Path
from typing import Callable, Optional

import structlog

from src.config.settings import Settings
from src.tmux.client import TmuxClient
from src.tmux.exceptions import TmuxResponseTimeoutError
from .responses import ClaudeResponse, StreamUpdate
from src.claude.parser import ResponseFormatter

logger = structlog.get_logger()


class TmuxClaudeIntegration:
    """Claude integration through tmux pane communication."""

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
        working_directory: Path,
        session_id: Optional[str] = None,
        continue_session: bool = False,
        stream_callback: Optional[Callable] = None,
    ) -> ClaudeResponse:
        """Execute command via tmux pane.
        
        Args:
            prompt: User prompt to send to Claude
            working_directory: Working directory (used for session context)
            session_id: Session ID for tracking
            continue_session: Whether to continue existing session
            stream_callback: Optional callback for streaming updates
            
        Returns:
            ClaudeResponse with the result
        """
        start_time = time.time()
        
        try:
            logger.info(
                "Executing tmux command",
                pane=self.tmux_client.pane_target,
                prompt_length=len(prompt),
                session_id=session_id,
            )

            # Use previous output as baseline, or capture current state if first time
            if not self.last_full_output:
                self.last_full_output = await self.tmux_client.capture_output(
                    self.config.tmux_capture_lines
                )
            
            initial_output = self.last_full_output

            # Send prompt to Claude
            await self.send_prompt(prompt, stream_callback)
            
            # Wait for response
            final_output = await self.wait_for_response(
                initial_output, 
                timeout=self.config.claude_timeout_seconds,
                stream_callback=stream_callback
            )
            
            # Extract only the new content since last interaction
            response_content = self._extract_new_response(initial_output, final_output)
            
            # Update our tracking of the last output
            self.last_full_output = final_output

            # Calculate metrics
            duration_ms = int((time.time() - start_time) * 1000)
            
            # Create response
            return ClaudeResponse(
                content=response_content,
                session_id=session_id or "tmux-session",
                cost=0.0,  # Cost tracking not available via tmux
                duration_ms=duration_ms,
                num_turns=1,  # Turn tracking not available via tmux
                is_error=False,
                tools_used=[],  # Tool tracking not available via tmux
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

    async def send_prompt(self, prompt: str, stream_callback: Optional[Callable] = None) -> None:
        """Send prompt to Claude in tmux.
        
        Args:
            prompt: User prompt to send
            stream_callback: Optional callback for streaming updates
        """
        if stream_callback:
            await stream_callback(StreamUpdate(
                type="system", 
                content=f"Sending prompt to tmux pane {self.tmux_client.pane_target}..."
            ))
        
        # Send the prompt
        await self.tmux_client.send_command(prompt)
        
        if stream_callback:
            await stream_callback(StreamUpdate(
                type="user", 
                content=prompt
            ))

    async def wait_for_response(
        self, 
        initial_output: str, 
        timeout: int = 30,
        stream_callback: Optional[Callable] = None
    ) -> str:
        """Wait for and capture Claude response.
        
        Args:
            initial_output: Initial pane content before sending prompt
            timeout: Maximum time to wait for response
            stream_callback: Optional callback for streaming updates
            
        Returns:
            Complete tmux output after response
            
        Raises:
            TmuxResponseTimeoutError: If no response within timeout
        """
        if stream_callback:
            await stream_callback(StreamUpdate(
                type="system", 
                content="Waiting for Claude response..."
            ))

        start_time = time.time()
        last_output = initial_output
        stable_count = 0
        
        while time.time() - start_time < timeout:
            # Capture current output
            current_output = await self.tmux_client.capture_output(
                self.config.tmux_capture_lines
            )
            
            # Check if output changed
            if current_output != last_output:
                stable_count = 0  # Reset stability counter
                last_output = current_output
                
                # Send progress update
                if stream_callback:
                    await stream_callback(StreamUpdate(
                        type="system", 
                        content="Receiving response..."
                    ))
            else:
                stable_count += 1
                
                # If output has been stable for 3 polling intervals, assume response is complete
                if stable_count >= 3:
                    # Check if we have the input box (indicates Claude is ready for next input)
                    if '╭' in current_output and '╰' in current_output:
                        return current_output
            
            await asyncio.sleep(self.config.tmux_poll_interval)
        
        # Timeout reached, return what we have
        final_output = await self.tmux_client.capture_output(
            self.config.tmux_capture_lines
        )
        return final_output

    def _extract_new_response(self, previous_output: str, current_output: str) -> str:
        """Extract only the new Claude response from tmux output.
        
        Args:
            previous_output: Output before sending the prompt
            current_output: Output after receiving response
            
        Returns:
            Only the new response content from Claude
        """
        # Parse both outputs to get clean content
        previous_clean = ResponseFormatter.parse_tmux_output(previous_output)
        current_clean = ResponseFormatter.parse_tmux_output(current_output)
        
        logger.debug(
            "Extracting new response",
            previous_len=len(previous_clean),
            current_len=len(current_clean),
            previous_lines=len(previous_clean.split('\n')) if previous_clean else 0,
            current_lines=len(current_clean.split('\n')) if current_clean else 0,
        )
        
        # If previous was empty, return current
        if not previous_clean.strip():
            logger.debug("Previous output was empty, returning current")
            return current_clean
        
        # Simple approach: if current is longer than previous, return the difference
        if len(current_clean) > len(previous_clean):
            # Check if previous content is a prefix of current
            if current_clean.startswith(previous_clean):
                # Return the new part
                new_content = current_clean[len(previous_clean):].lstrip('\n')
                return new_content
        
        # More sophisticated line-based approach
        previous_lines = previous_clean.split('\n')
        current_lines = current_clean.split('\n')
        
        # Find the longest common suffix between previous and current
        # This handles cases where content might be inserted in the middle
        common_suffix_len = 0
        for i in range(1, min(len(previous_lines), len(current_lines)) + 1):
            if (previous_lines[-i].strip() == current_lines[-i].strip() and 
                previous_lines[-i].strip()):  # Don't match on empty lines
                common_suffix_len = i
            else:
                break
        
        # If we found a common suffix, extract content before it
        if common_suffix_len > 0:
            # Find where previous content ends in current output
            previous_end = len(current_lines) - common_suffix_len
            
            # Look backwards from that point to find where previous content actually ends
            for i in range(previous_end - 1, -1, -1):
                line = current_lines[i].strip()
                # Check if this line exists in previous output
                if any(line == prev_line.strip() for prev_line in previous_lines if prev_line.strip()):
                    # New content starts after this line
                    new_lines = current_lines[i + 1:previous_end]
                    break
            else:
                # No match found, take everything before common suffix
                new_lines = current_lines[:previous_end]
        else:
            # No common suffix, try to find where new content starts
            new_content_start = 0
            
            # Look for the end of previous content in current output
            if previous_lines:
                last_prev_line = previous_lines[-1].strip()
                if last_prev_line:
                    for i, curr_line in enumerate(current_lines):
                        if curr_line.strip() == last_prev_line:
                            new_content_start = i + 1
                            break
            
            new_lines = current_lines[new_content_start:]
        
        # Clean up the new lines - only remove empty lines at the start
        while new_lines and not new_lines[0].strip():
            new_lines.pop(0)
        # Don't remove lines at the end - they might be important content
        
        if new_lines:
            return '\n'.join(new_lines)
        
        # Final fallback: if current differs from previous, return current
        if current_clean != previous_clean:
            return current_clean
        
        return "No new response detected"

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