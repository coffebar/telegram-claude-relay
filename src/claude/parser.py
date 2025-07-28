"""Parse Claude Code output formats.

Features:
- JSON parsing
- Stream parsing
- Error detection
- Tool extraction
"""

import json
import re
from typing import Any, Dict, List

import structlog

from .exceptions import ClaudeParsingError

logger = structlog.get_logger()


class OutputParser:
    """Parse various Claude Code output formats."""

    @staticmethod
    def parse_json_output(output: str) -> Dict[str, Any]:
        """Parse single JSON output."""
        try:
            return json.loads(output)
        except json.JSONDecodeError as e:
            logger.error(
                "Failed to parse JSON output", output=output[:200], error=str(e)
            )
            raise ClaudeParsingError(f"Failed to parse JSON output: {e}")

    @staticmethod
    def parse_stream_json(lines: List[str]) -> List[Dict[str, Any]]:
        """Parse streaming JSON output."""
        messages = []

        for line in lines:
            line = line.strip()
            if not line:
                continue

            try:
                msg = json.loads(line)
                messages.append(msg)
            except json.JSONDecodeError:
                logger.warning("Skipping invalid JSON line", line=line)
                continue

        return messages

    @staticmethod
    def extract_code_blocks(content: str) -> List[Dict[str, str]]:
        """Extract code blocks from response."""
        code_blocks = []
        pattern = r"```(\w+)?\n(.*?)```"

        for match in re.finditer(pattern, content, re.DOTALL):
            language = match.group(1) or "text"
            code = match.group(2).strip()

            code_blocks.append({"language": language, "code": code})

        logger.debug("Extracted code blocks", count=len(code_blocks))
        return code_blocks

    @staticmethod
    def extract_file_operations(messages: List[Dict]) -> List[Dict[str, Any]]:
        """Extract file operations from tool calls."""
        file_ops = []

        for msg in messages:
            if msg.get("type") != "assistant":
                continue

            message = msg.get("message", {})
            for block in message.get("content", []):
                if block.get("type") != "tool_use":
                    continue

                tool_name = block.get("name", "")
                tool_input = block.get("input", {})

                # Check for file-related tools
                if tool_name in [
                    "create_file",
                    "edit_file",
                    "read_file",
                    "Write",
                    "Edit",
                    "Read",
                ]:
                    file_ops.append(
                        {
                            "operation": tool_name,
                            "path": tool_input.get("path")
                            or tool_input.get("file_path"),
                            "content": tool_input.get("content")
                            or tool_input.get("new_string"),
                            "old_content": tool_input.get("old_string"),
                            "timestamp": msg.get("timestamp"),
                        }
                    )

        logger.debug("Extracted file operations", count=len(file_ops))
        return file_ops

    @staticmethod
    def extract_shell_commands(messages: List[Dict]) -> List[Dict[str, Any]]:
        """Extract shell commands from tool calls."""
        shell_commands = []

        for msg in messages:
            if msg.get("type") != "assistant":
                continue

            message = msg.get("message", {})
            for block in message.get("content", []):
                if block.get("type") != "tool_use":
                    continue

                tool_name = block.get("name", "")
                tool_input = block.get("input", {})

                # Check for shell/bash tools
                if tool_name in ["bash", "shell", "Bash"]:
                    shell_commands.append(
                        {
                            "operation": tool_name,
                            "command": tool_input.get("command"),
                            "description": tool_input.get("description"),
                            "timestamp": msg.get("timestamp"),
                        }
                    )

        logger.debug("Extracted shell commands", count=len(shell_commands))
        return shell_commands

    @staticmethod
    def extract_response_text(messages: List[Dict]) -> str:
        """Extract all text content from assistant messages."""
        text_parts = []

        for msg in messages:
            if msg.get("type") != "assistant":
                continue

            message = msg.get("message", {})
            for block in message.get("content", []):
                if block.get("type") == "text":
                    text_parts.append(block.get("text", ""))

        return "\n".join(text_parts)

    @staticmethod
    def extract_tool_results(messages: List[Dict]) -> List[Dict[str, Any]]:
        """Extract tool results from tool_result messages."""
        tool_results = []

        for msg in messages:
            if msg.get("type") == "tool_result":
                result = msg.get("result", {})
                tool_results.append(
                    {
                        "tool_use_id": msg.get("tool_use_id"),
                        "content": result.get("content"),
                        "is_error": result.get("is_error", False),
                        "timestamp": msg.get("timestamp"),
                    }
                )

        logger.debug("Extracted tool results", count=len(tool_results))
        return tool_results

    @staticmethod
    def detect_errors(messages: List[Dict]) -> List[Dict[str, Any]]:
        """Detect errors in message stream."""
        errors = []

        for msg in messages:
            # Check for error messages
            if msg.get("is_error") or msg.get("type") == "error":
                errors.append(
                    {
                        "type": msg.get("type", "unknown"),
                        "subtype": msg.get("subtype"),
                        "message": msg.get("message", str(msg)),
                        "timestamp": msg.get("timestamp"),
                    }
                )

            # Check for tool result errors
            if msg.get("type") == "tool_result":
                result = msg.get("result", {})
                if result.get("is_error"):
                    errors.append(
                        {
                            "type": "tool_error",
                            "tool_use_id": msg.get("tool_use_id"),
                            "message": result.get("content", "Tool execution failed"),
                            "timestamp": msg.get("timestamp"),
                        }
                    )

        logger.debug("Detected errors", count=len(errors))
        return errors

    @staticmethod
    def summarize_session(messages: List[Dict]) -> Dict[str, Any]:
        """Create a summary of the session."""
        summary = {
            "total_messages": len(messages),
            "assistant_messages": 0,
            "user_messages": 0,
            "tool_calls": 0,
            "tool_results": 0,
            "errors": 0,
            "code_blocks": 0,
            "file_operations": 0,
            "shell_commands": 0,
        }

        full_text = ""

        for msg in messages:
            msg_type = msg.get("type")

            if msg_type == "assistant":
                summary["assistant_messages"] += 1

                # Extract text for analysis
                message = msg.get("message", {})
                for block in message.get("content", []):
                    if block.get("type") == "text":
                        full_text += block.get("text", "") + "\n"
                    elif block.get("type") == "tool_use":
                        summary["tool_calls"] += 1

            elif msg_type == "user":
                summary["user_messages"] += 1

            elif msg_type == "tool_result":
                summary["tool_results"] += 1

            elif msg.get("is_error") or msg_type == "error":
                summary["errors"] += 1

        # Analyze extracted content
        summary["code_blocks"] = len(OutputParser.extract_code_blocks(full_text))
        summary["file_operations"] = len(OutputParser.extract_file_operations(messages))
        summary["shell_commands"] = len(OutputParser.extract_shell_commands(messages))

        return summary


class ResponseFormatter:
    """Format Claude responses for Telegram display."""

    def __init__(self, max_message_length: int = 4000):
        """Initialize formatter."""
        self.max_message_length = max_message_length

    def format_response(self, content: str, include_metadata: bool = True) -> List[str]:
        """Format response content into Telegram messages."""
        if not content.strip():
            return ["_(Empty response)_"]

        # Split by code blocks first to preserve them
        parts = self._split_preserving_code_blocks(content)

        messages = []
        for part in parts:
            if len(part) <= self.max_message_length:
                messages.append(part)
            else:
                # Split long parts
                messages.extend(self._split_long_text(part))

        # Ensure we have at least one message
        if not messages:
            messages = ["_(No content to display)_"]

        return messages

    def _split_preserving_code_blocks(self, text: str) -> List[str]:
        """Split text while preserving code blocks."""
        parts = []
        current_part = ""
        in_code_block = False

        lines = text.split("\n")

        for line in lines:
            # Check for code block markers
            if line.strip().startswith("```"):
                in_code_block = not in_code_block

            line_with_newline = line + "\n"

            # If adding this line would exceed limit and we're not in a code block
            if (
                len(current_part + line_with_newline) > self.max_message_length
                and not in_code_block
                and current_part.strip()
            ):
                parts.append(current_part.rstrip())
                current_part = line_with_newline
            else:
                current_part += line_with_newline

        if current_part.strip():
            parts.append(current_part.rstrip())

        return parts

    def _split_long_text(self, text: str) -> List[str]:
        """Split text that's too long for a single message."""
        parts = []
        current = ""

        for char in text:
            if len(current + char) > self.max_message_length:
                if current:
                    parts.append(current)
                    current = char
                else:
                    # Single character somehow exceeds limit
                    parts.append(char)
                    current = ""
            else:
                current += char

        if current:
            parts.append(current)

        return parts

    @staticmethod
    def parse_tmux_output(raw_output: str) -> str:
        """Parse tmux terminal output to extract Claude's response.
        
        Args:
            raw_output: Raw tmux pane capture output
            
        Returns:
            Cleaned Claude response text
        """
        lines = raw_output.split('\n')
        
        # Find the input box boundaries (Claude's interactive prompt)
        input_box_start = -1
        input_box_end = -1
        
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith('╭') and '─' in stripped:
                input_box_start = i
            elif stripped.startswith('╰') and '─' in stripped and input_box_start != -1:
                input_box_end = i
                break
        
        # Extract content before the input box
        if input_box_start > 0:
            response_lines = lines[:input_box_start]
        else:
            response_lines = lines
        
        # Find the start of Claude's response by looking for common patterns
        start_index = 0
        
        # Look for user input line (often ends with continuation prompts)
        for i in range(len(response_lines) - 1, -1, -1):
            line = response_lines[i].strip()
            if line and not line.startswith(('>', '│', '╭', '╰', '?')):
                # This might be user input, Claude's response starts after
                start_index = i + 1
                break
        
        # Extract response content
        response_lines = response_lines[start_index:]
        
        # Clean up the response
        # Remove empty lines at the beginning
        while response_lines and not response_lines[0].strip():
            response_lines.pop(0)
        
        
        # Only remove trailing help text like "? for shortcuts" - preserve other content
        if response_lines and response_lines[-1].strip() == "? for shortcuts":
            response_lines.pop()
        
        # Remove only trailing empty lines, but preserve lines with content
        while response_lines and not response_lines[-1].strip():
            response_lines.pop()
        
        # Join the cleaned response
        response = '\n'.join(response_lines)
        
        logger.debug(
            "Parsed tmux output",
            input_box_start=input_box_start,
            total_lines=len(lines),
            response_lines_count=len(response_lines),
            response_length=len(response),
            response_preview=response[:100] + "..." if len(response) > 100 else response
        )
        
        # If response is still empty or too short, return a fallback
        if not response.strip() or len(response.strip()) < 10:
            # Return content before input box as fallback
            if input_box_start > 0:
                fallback_lines = lines[:input_box_start]
                # Only remove the very last line if it's "? for shortcuts"
                if (fallback_lines and 
                    fallback_lines[-1].strip() == "? for shortcuts"):
                    fallback_lines = fallback_lines[:-1]
                response = '\n'.join(fallback_lines).strip()
        
        return response.strip()

