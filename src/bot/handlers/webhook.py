"""Webhook handler for receiving Claude hook events and providing live status updates."""

import asyncio
from typing import Dict, Any, Optional

import structlog
from telegram import Bot
from telegram.constants import ParseMode

from ...config.settings import Settings

logger = structlog.get_logger()


class MessageTracker:
    """Tracks the last status messages for live-updating."""
    
    def __init__(self):
        self.last_status_messages: Dict[int, Dict[str, Any]] = {}  # user_id -> {message_id, chat_id, type}
    
    def get_message_type(self, message: str) -> str:
        """Determine the type of message based on content."""
        if "💬 **New Prompt:**" in message:
            return "prompt"
        elif any(prefix in message for prefix in ["✏️", "📝", "👁️", "💻", "🔍", "🔧"]):
            return "pre_tool"
        elif "✅" in message:
            return "post_tool"
        elif "🤖 **Claude:**" in message:
            return "response"
        else:
            return "other"
    
    def should_edit_last_message(self, user_id: int, message_type: str) -> tuple[bool, Optional[Dict[str, Any]]]:
        """Check if we should edit the last message instead of sending a new one."""
        last_msg = self.last_status_messages.get(user_id)
        
        logger.debug("Checking message edit", 
                    user_id=user_id, 
                    message_type=message_type, 
                    has_last_msg=bool(last_msg),
                    last_type=last_msg.get("type") if last_msg else None)
        
        if not last_msg:
            logger.debug("No last message found, will send new")
            return False, None
        
        last_type = last_msg.get("type")
        
        # Clear status tracking for new prompts or responses (start fresh)
        if message_type in ["prompt", "response"]:
            logger.debug("Clearing status tracking for new prompt/response")
            self.last_status_messages.pop(user_id, None)
            return False, None
        
        # Edit if both are tool-related messages
        if message_type in ["pre_tool", "post_tool"] and last_type in ["pre_tool", "post_tool"]:
            logger.debug("Will edit last message", last_msg_id=last_msg.get("message_id"))
            return True, last_msg
        
        logger.debug("Will send new message", reason=f"type mismatch: {last_type} -> {message_type}")
        return False, None
    
    def track_message(self, user_id: int, message_id: int, chat_id: int, message_type: str) -> None:
        """Track a message for potential editing."""
        if message_type in ["pre_tool", "post_tool"]:
            self.last_status_messages[user_id] = {
                "message_id": message_id,
                "chat_id": chat_id,
                "type": message_type
            }


class ConversationWebhookHandler:
    """Handles incoming webhook requests with Claude conversation updates."""
    
    def __init__(self, bot: Bot, settings: Settings):
        self.bot = bot
        self.settings = settings
        self.session_to_chat: Dict[str, int] = {}  # session_id -> chat_id mapping
        self.subscribed_users: set[int] = set()  # Track subscribed users
        self.last_telegram_prompts: Dict[int, str] = {}  # user_id -> last prompt sent via Telegram
        self.message_tracker = MessageTracker()
        
    async def handle_conversation_update(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Process incoming conversation update from Claude hook."""
        logger.info("Webhook handler called", data_keys=list(data.keys()))
        try:
            session_id = data.get("session_id")
            message = data.get("message", {})
            
            logger.info("Processing conversation update", session_id=session_id, message_type=message.get("type"), role=message.get("role"))
            
            if not session_id or not message:
                logger.warning("Missing required fields", session_id=session_id, has_message=bool(message))
                return {"status": "error", "message": "Missing required fields"}
            
            # Format the message for Telegram
            formatted_message = self._format_message(message)
            
            if formatted_message:
                logger.info("Sending formatted message to Telegram", session_id=session_id, msg_len=len(formatted_message))
                # Send to all active chats for this session
                await self._send_to_telegram(session_id, formatted_message)
            else:
                logger.info("Message filtered out during formatting", message_type=message.get("type"), role=message.get("role"))
                
            return {"status": "ok"}
            
        except Exception as e:
            logger.error("Error handling conversation update", error=str(e))
            return {"status": "error", "message": str(e)}
    
    def _format_message(self, message: Dict[str, Any]) -> Optional[str]:
        """Format Claude message for Telegram display."""
        msg_type = message.get("type")
        role = message.get("role")
        content = message.get("content", "")
        
        if not content:
            return None
            
        # Format based on message type
        if msg_type == "thinking":
            # Show thinking steps in italic
            return f"💭 _Thinking: {content}_"
            
        elif msg_type == "message" and role == "assistant":
            # Regular Claude response
            if len(content) > 3000:
                # Truncate very long messages
                content = content[:3000] + "\n\n... (truncated)"
            return f"🤖 **Claude:**\n{content}"
            
        elif msg_type == "message" and role == "user":
            # Echo user messages (optional)
            return None  # Skip user messages as they're already in Telegram
            
        elif msg_type == "hook_notification" and role == "system":
            # Check if this is a "New Prompt" notification that matches a recent Telegram prompt
            if "💬 **New Prompt:**" in content:
                # Extract the prompt content from the formatted message
                prompt_start = content.find("```\n") + 4
                prompt_end = content.rfind("\n```")
                if prompt_start > 3 and prompt_end > prompt_start:
                    hook_prompt = content[prompt_start:prompt_end]
                    
                    # Check if this matches any recent Telegram prompt
                    for user_id, last_prompt in self.last_telegram_prompts.items():
                        if hook_prompt.strip() == last_prompt.strip():
                            logger.info("Skipping echo of Telegram prompt", user_id=user_id, prompt_length=len(hook_prompt))
                            return None  # Skip this notification
            
            # Real-time hook notifications
            return content  # Already formatted by ConversationMonitor
            
        # Tool calls
        tool_calls = message.get("tool_calls", [])
        if tool_calls:
            tools_text = self._format_tool_calls(tool_calls)
            if tools_text:
                return f"🔧 **Tools Used:**\n{tools_text}"
                
        return None
    
    def _format_tool_calls(self, tool_calls: list) -> str:
        """Format tool calls for display."""
        formatted = []
        for tool in tool_calls:
            name = tool.get("name", "Unknown")
            params = tool.get("parameters", {})
            
            # Format based on tool type
            if name == "Edit":
                file_path = params.get("file_path", "")
                formatted.append(f"✏️ Edited: `{file_path}`")
            elif name == "Write":
                file_path = params.get("file_path", "")
                formatted.append(f"📝 Created: `{file_path}`")
            elif name == "Read":
                file_path = params.get("file_path", "")
                formatted.append(f"👁️ Read: `{file_path}`")
            elif name == "Bash":
                command = params.get("command", "")
                if len(command) > 50:
                    command = command[:50] + "..."
                formatted.append(f"💻 Ran: `{command}`")
            else:
                formatted.append(f"🔧 {name}")
                
        return "\n".join(formatted)
    
    async def _send_to_telegram(self, session_id: str, message: str) -> None:
        """Send message to all relevant Telegram chats, editing status messages when possible."""
        # Send to all subscribed users
        # If no users are subscribed yet, fall back to allowed users
        users_to_notify = self.subscribed_users if self.subscribed_users else (self.settings.allowed_users or [])
        
        # Determine message type
        message_type = self.message_tracker.get_message_type(message)
        
        logger.debug("Processing webhook message", 
                    message_type=message_type, 
                    user_count=len(users_to_notify),
                    message_preview=message[:50])
        
        for user_id in users_to_notify:
            try:
                # Check if we should edit the last message or send a new one
                should_edit, last_msg = self.message_tracker.should_edit_last_message(user_id, message_type)
                
                if should_edit and last_msg:
                    # Edit the existing message
                    try:
                        logger.debug("Attempting to edit message", 
                                   user_id=user_id,
                                   message_id=last_msg["message_id"],
                                   message_preview=message[:50])
                        edited_msg = await self.bot.edit_message_text(
                            chat_id=last_msg["chat_id"],
                            message_id=last_msg["message_id"],
                            text=message,
                            parse_mode=ParseMode.MARKDOWN
                        )
                        logger.debug("Successfully edited message", 
                                   user_id=user_id,
                                   message_id=edited_msg.message_id)
                        # Update the stored message info
                        self.message_tracker.track_message(user_id, edited_msg.message_id, user_id, message_type)
                        # Small delay to prevent rate limiting
                        await asyncio.sleep(0.1)
                    except Exception as edit_error:
                        logger.warning("Failed to edit message, sending new one", 
                                     user_id=user_id, 
                                     message_id=last_msg.get("message_id"),
                                     error=str(edit_error))
                        # Fall back to sending a new message
                        await self._send_new_message(user_id, message, message_type)
                else:
                    # Send a new message
                    await self._send_new_message(user_id, message, message_type)
                    
            except Exception as e:
                logger.warning(
                    "Failed to send update to user",
                    user_id=user_id,
                    error=str(e)
                )
    
    async def _send_new_message(self, user_id: int, message: str, message_type: str) -> None:
        """Send a new message and track it."""
        sent_msg = await self.bot.send_message(
            chat_id=user_id,
            text=message,
            parse_mode=ParseMode.MARKDOWN
        )
        
        # Store the message info for potential editing
        self.message_tracker.track_message(user_id, sent_msg.message_id, user_id, message_type)
    
    def register_session(self, session_id: str, chat_id: int) -> None:
        """Register a session with a chat ID for targeted updates."""
        self.session_to_chat[session_id] = chat_id
        logger.info("Registered session", session_id=session_id, chat_id=chat_id)
    
    def record_telegram_prompt(self, user_id: int, prompt: str) -> None:
        """Record a prompt sent from Telegram to prevent echo."""
        self.last_telegram_prompts[user_id] = prompt
        logger.debug("Recorded Telegram prompt", user_id=user_id, prompt_length=len(prompt))
    
    async def initialize_subscriptions(self) -> None:
        """Initialize subscriptions for all allowed users."""
        if not self.settings.allowed_users:
            logger.warning("No allowed users configured")
            return
            
        logger.info("Initializing hook message subscriptions for allowed users", 
                   user_count=len(self.settings.allowed_users))
        
        # Subscribe all allowed users
        for user_id in self.settings.allowed_users:
            self.subscribed_users.add(user_id)
            
        # Notify users that they are subscribed
        notification_message = (
            "🔔 **Hook Monitoring Enabled**\n\n"
            "You are now subscribed to receive Claude conversation updates.\n"
            "You'll receive notifications when Claude uses tools or performs actions."
        )
        
        success_count = 0
        for user_id in self.subscribed_users:
            try:
                await self.bot.send_message(
                    chat_id=user_id,
                    text=notification_message,
                    parse_mode=ParseMode.MARKDOWN
                )
                success_count += 1
                logger.info("Notified user about hook subscription", user_id=user_id)
            except Exception as e:
                logger.warning(
                    "Failed to notify user about subscription",
                    user_id=user_id,
                    error=str(e)
                )
                
        logger.info("Hook subscription initialization complete", 
                   subscribed=len(self.subscribed_users),
                   notified=success_count)