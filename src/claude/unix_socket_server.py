"""Unix socket server for secure IPC with Claude hooks."""

import os
import json
import asyncio
from pathlib import Path
from typing import Optional, Dict, Any

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
            self.handle_client,
            path=str(self.socket_path)
        )
        
        # Set permissions to be restrictive (only owner can access)
        os.chmod(self.socket_path, 0o600)
        
        logger.info(f"Unix socket server started at {self.socket_path}")
        
        async with self.server:
            await self.server.serve_forever()
    
    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Handle incoming connections."""
        try:
            # Read data from the socket
            data = await reader.read(65536)  # 64KB max
            
            if not data:
                return
                
            # Parse JSON data
            try:
                hook_data = json.loads(data.decode('utf-8'))
            except json.JSONDecodeError as e:
                logger.error("Invalid JSON received", error=str(e))
                response = {"status": "error", "message": "Invalid JSON"}
                writer.write(json.dumps(response).encode('utf-8'))
                await writer.drain()
                return
            
            # Process the hook event
            response = await self.process_hook_event(hook_data)
            
            # Send response
            writer.write(json.dumps(response).encode('utf-8'))
            await writer.drain()
            
        except Exception as e:
            logger.error("Error handling client connection", error=str(e))
        finally:
            writer.close()
            await writer.wait_closed()
    
    async def process_hook_event(self, hook_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process incoming hook event."""
        hook_type = hook_data.get("hook_event_name")
        logger.info("Received hook event", hook_type=hook_type, data_keys=list(hook_data.keys()))
        
        if hook_type == "Stop":
            session_id = hook_data.get("session_id")
            transcript_path = hook_data.get("transcript_path")
            
            logger.info("Processing Stop hook", session_id=session_id, transcript_path=transcript_path)
            
            if not session_id or not transcript_path:
                logger.error("Missing required fields", session_id=session_id, transcript_path=transcript_path)
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
            
            logger.info("Processing UserPromptSubmit hook", 
                       session_id=session_id, 
                       prompt_length=len(prompt))
            
            # Send notification about new prompt
            asyncio.create_task(
                self.monitor.send_hook_notification({
                    "type": "user_prompt",
                    "session_id": session_id,
                    "prompt": prompt,
                    "timestamp": hook_data.get("timestamp")
                })
            )
            
            return {"continue": True}
        
        elif hook_type == "PreToolUse":
            # Handle pre-tool use notification
            tool_name = hook_data.get("tool_name", "")
            parameters = hook_data.get("parameters", {})
            session_id = hook_data.get("session_id", "unknown")
            
            logger.info("Processing PreToolUse hook", 
                       session_id=session_id,
                       tool_name=tool_name)
            
            # Send notification about tool use
            asyncio.create_task(
                self.monitor.send_hook_notification({
                    "type": "pre_tool_use",
                    "session_id": session_id,
                    "tool_name": tool_name,
                    "parameters": parameters,
                    "timestamp": hook_data.get("timestamp")
                })
            )
            
            return {"continue": True}
        
        elif hook_type == "PostToolUse":
            # Handle post-tool use notification
            tool_name = hook_data.get("tool_name", "")
            result = hook_data.get("result", {})
            session_id = hook_data.get("session_id", "unknown")
            
            logger.info("Processing PostToolUse hook",
                       session_id=session_id,
                       tool_name=tool_name,
                       has_result=bool(result))
            
            # Send notification about tool result
            asyncio.create_task(
                self.monitor.send_hook_notification({
                    "type": "post_tool_use",
                    "session_id": session_id,
                    "tool_name": tool_name,
                    "result_preview": str(result)[:200] if result else None,
                    "timestamp": hook_data.get("timestamp")
                })
            )
            
            return {"continue": True}
        
        return {"status": "error", "message": f"Unknown hook type: {hook_type}"}
    
    async def stop(self):
        """Stop the server and cleanup."""
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            
        # Remove socket file
        if self.socket_path.exists():
            self.socket_path.unlink()