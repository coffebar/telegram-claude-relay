"""Main Telegram bot class with Claude Code hook integration.

Features:
- Command registration and message handling
- Claude Code hook monitoring integration
- Context injection and dependency management
- Real-time tool status updates
- Graceful shutdown
"""

import asyncio

from typing import Any, Callable, Dict, Optional

import structlog

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from ..config.settings import Settings
from ..exceptions import ClaudeCodeTelegramError
from .command_discovery import CommandDiscovery


logger = structlog.get_logger()


class ClaudeTelegramBot:
    """Main bot orchestrator."""

    def __init__(self, settings: Settings, dependencies: Dict[str, Any]):
        """Initialize bot with settings and dependencies."""
        self.settings = settings
        self.deps = dependencies
        self.app: Optional[Application] = None
        self.is_running = False
        self.command_discovery: Optional[CommandDiscovery] = None

    async def initialize(self) -> None:
        """Initialize bot application."""
        logger.info("Initializing Telegram bot")

        # Initialize command discovery
        await self._initialize_command_discovery()

        # Create application
        builder = Application.builder()
        builder.token(self.settings.telegram_token_str)

        # Configure connection settings
        builder.connect_timeout(30)
        builder.read_timeout(30)
        builder.write_timeout(30)
        builder.pool_timeout(30)

        self.app = builder.build()

        # Set bot commands for menu
        await self._set_bot_commands()

        # Register handlers
        await self._register_handlers()

        # Add middleware
        self._add_middleware()

        # Set error handler
        self.app.add_error_handler(self._error_handler)

        logger.info("Bot initialization complete")

    async def _initialize_command_discovery(self) -> None:
        """Initialize command discovery system."""
        # Get project CWD from Claude integration if available
        project_cwd = None
        claude_integration = self.deps.get("claude_integration")

        if claude_integration:
            try:
                # Ensure tmux integration is set up to get CWD
                await claude_integration._ensure_tmux_integration()
                if (
                    claude_integration.tmux_integration
                    and claude_integration.tmux_integration.tmux_client
                ):
                    project_cwd = (
                        await claude_integration.tmux_integration.tmux_client.get_pane_cwd()
                    )
                    logger.info(
                        "Got project CWD from Claude integration",
                        project_cwd=project_cwd,
                    )
            except Exception as e:
                logger.warning(
                    "Could not get project CWD from Claude integration", error=str(e)
                )

        # Initialize command discovery
        self.command_discovery = CommandDiscovery(project_cwd)

        # Discover commands
        await self.command_discovery.discover_commands()

    async def _set_bot_commands(self) -> None:
        """Set bot command menu including discovered commands."""
        from telegram import BotCommand, BotCommandScopeChat

        # Start with built-in commands
        commands = [
            BotCommand("clear", "Clear Claude's conversation history"),
            BotCommand("compact", "Compact Claude's conversation"),
            BotCommand("esc", "Send ESC key to Claude (cancel current operation)"),
            BotCommand("self_update", "Update bot from GitHub and restart"),
        ]

        # Add discovered commands if available
        if self.command_discovery:
            discovered_commands = await self.command_discovery.discover_commands()
            for command_name, metadata in discovered_commands.items():
                commands.append(BotCommand(command_name, metadata["description"]))

        # Set commands for each allowed user
        # This provides better privacy and ensures only authorized users see commands
        for user_id in self.settings.allowed_users:
            try:
                # Use BotCommandScopeChat to set commands for specific user
                scope = BotCommandScopeChat(chat_id=user_id)
                await self.app.bot.set_my_commands(commands, scope=scope)
                logger.debug("Set commands for user", user_id=user_id)
            except Exception as e:
                logger.warning(
                    "Failed to set commands for user", user_id=user_id, error=str(e)
                )

        logger.info(
            "Bot commands set for authorized users",
            total_commands=len(commands),
            commands=[cmd.command for cmd in commands],
            user_count=len(self.settings.allowed_users),
        )

    async def _register_handlers(self) -> None:
        """Register all command and message handlers."""
        from .handlers import command, message

        # Register built-in command handlers
        handlers = [
            ("start", command.start_command),
            ("clear", command.clear_command),
            ("compact", command.compact_command),
            ("esc", command.esc_command),
            ("self_update", command.self_update_command),
        ]

        for cmd, handler in handlers:
            self.app.add_handler(CommandHandler(cmd, self._inject_deps(handler)))

        # Register dynamic command handlers
        if self.command_discovery:
            discovered_commands = await self.command_discovery.discover_commands()
            for command_name in discovered_commands.keys():
                self.app.add_handler(
                    CommandHandler(
                        command_name,
                        self._inject_deps(
                            self._create_dynamic_command_handler(command_name)
                        ),
                    )
                )

        self.app.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                self._inject_deps(message.handle_text_message),
            ),
            group=10,
        )

        # Add callback query handler for permission dialogs
        self.app.add_handler(
            CallbackQueryHandler(self._inject_deps(self._handle_callback_query))
        )

        logger.info("Bot handlers registered")

    def _create_dynamic_command_handler(self, command_name: str) -> Callable:
        """Create a handler for a dynamically discovered command.

        Args:
            command_name: Name of the command to handle

        Returns:
            Async function that handles the command
        """

        async def dynamic_command_handler(
            update: Update, context: ContextTypes.DEFAULT_TYPE
        ) -> None:
            """Handle dynamically discovered command - forwards to Claude."""
            # Import here to avoid circular imports
            from .handlers.command import _forward_claude_command

            # Forward the command to Claude using the existing infrastructure
            await _forward_claude_command(update, context, f"/{command_name}")

        return dynamic_command_handler

    def _inject_deps(self, handler: Callable) -> Callable:
        """Inject dependencies into handlers."""

        async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE):
            # Add dependencies to context
            for key, value in self.deps.items():
                context.bot_data[key] = value

            # Add settings
            context.bot_data["settings"] = self.settings

            return await handler(update, context)

        return wrapped

    async def _handle_callback_query(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle callback queries (inline keyboard button presses)."""
        callback_query = update.callback_query

        if not callback_query:
            return

        # Check if this is a permission dialog callback
        if callback_query.data and callback_query.data.startswith("perm_"):
            webhook_handler = context.bot_data.get("webhook_handler")
            if webhook_handler:
                await webhook_handler.handle_permission_callback(
                    callback_query, context
                )
            else:
                await callback_query.answer("Webhook handler not available.")
        else:
            # Handle other types of callbacks if needed
            await callback_query.answer("Unknown callback.")

    def _add_middleware(self) -> None:
        """Add middleware to application."""
        from .middleware.auth import auth_middleware
        from .middleware.rate_limit import rate_limit_middleware

        # Middleware runs in order of group numbers (lower = earlier)
        # Authentication first
        self.app.add_handler(
            MessageHandler(
                filters.ALL, self._create_middleware_handler(auth_middleware)
            ),
            group=-2,
        )

        # Rate limiting second
        self.app.add_handler(
            MessageHandler(
                filters.ALL, self._create_middleware_handler(rate_limit_middleware)
            ),
            group=-1,
        )

        logger.info("Middleware added to bot")

    def _create_middleware_handler(self, middleware_func: Callable) -> Callable:
        """Create middleware handler that injects dependencies."""

        async def middleware_wrapper(
            update: Update, context: ContextTypes.DEFAULT_TYPE
        ):
            # Inject dependencies into context
            for key, value in self.deps.items():
                context.bot_data[key] = value
            context.bot_data["settings"] = self.settings

            # Create a dummy handler that does nothing (middleware will handle everything)
            async def dummy_handler(event, data):
                return None

            # Call middleware with Telegram-style parameters
            return await middleware_func(dummy_handler, update, context.bot_data)

        return middleware_wrapper

    async def start(self) -> None:
        """Start the bot."""
        if self.is_running:
            logger.warning("Bot is already running")
            return

        await self.initialize()

        logger.info(
            "Starting bot", mode="webhook" if self.settings.webhook_url else "polling"
        )

        # Check git status and notify users on startup
        asyncio.create_task(self._check_git_updates_on_startup())

        try:
            self.is_running = True

            if self.settings.webhook_url:
                # Webhook mode
                await self.app.run_webhook(
                    listen="0.0.0.0",
                    port=self.settings.webhook_port,
                    url_path=self.settings.webhook_path,
                    webhook_url=self.settings.webhook_url,
                    drop_pending_updates=True,
                    allowed_updates=Update.ALL_TYPES,
                )
            else:
                # Polling mode - initialize and start polling manually
                await self.app.initialize()
                await self.app.start()
                await self.app.updater.start_polling(
                    allowed_updates=Update.ALL_TYPES,
                    drop_pending_updates=True,
                )

                # Keep running until manually stopped
                while self.is_running:
                    await asyncio.sleep(1)
        except Exception as e:
            logger.error("Error running bot", error=str(e))
            raise ClaudeCodeTelegramError(f"Failed to start bot: {str(e)}") from e
        finally:
            self.is_running = False

    async def stop(self) -> None:
        """Gracefully stop the bot."""
        if not self.is_running:
            logger.warning("Bot is not running")
            return

        logger.info("Stopping bot")

        try:
            self.is_running = False  # Stop the main loop first

            if self.app:
                # Stop the updater if it's running
                if self.app.updater.running:
                    await self.app.updater.stop()

                # Stop the application
                await self.app.stop()
                await self.app.shutdown()

            logger.info("Bot stopped successfully")
        except Exception as e:
            logger.error("Error stopping bot", error=str(e))
            raise ClaudeCodeTelegramError(f"Failed to stop bot: {str(e)}") from e

    async def _error_handler(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle errors globally."""
        error = context.error
        logger.error(
            "Global error handler triggered",
            error=str(error),
            update_type=type(update).__name__ if update else None,
            user_id=(
                update.effective_user.id if update and update.effective_user else None
            ),
        )

        # Determine error message for user
        from ..exceptions import (
            AuthenticationError,
            ConfigurationError,
            RateLimitExceeded,
            SecurityError,
        )

        error_messages = {
            AuthenticationError: "🔒 Authentication required. Please contact the administrator.",
            SecurityError: "🛡️ Security violation detected. This incident has been logged.",
            RateLimitExceeded: "⏱️ Rate limit exceeded. Please wait before sending more messages.",
            ConfigurationError: "⚙️ Configuration error. Please contact the administrator.",
            asyncio.TimeoutError: "⏰ Operation timed out. Please try again with a simpler request.",
        }

        error_type = type(error)
        user_message = error_messages.get(
            error_type, "❌ An unexpected error occurred. Please try again."
        )

        # Try to notify user
        if update and update.effective_message:
            try:
                await update.effective_message.reply_text(user_message)
            except Exception:
                logger.exception("Failed to send error message to user")

        # Log system error details
        if update and update.effective_user:
            logger.error(
                "System error for user",
                user_id=update.effective_user.id,
                error_type=error_type.__name__,
                error_message=str(error),
            )

    async def get_bot_info(self) -> Dict[str, Any]:
        """Get bot information."""
        if not self.app:
            return {"status": "not_initialized"}

        try:
            me = await self.app.bot.get_me()
            return {
                "status": "running" if self.is_running else "initialized",
                "username": me.username,
                "first_name": me.first_name,
                "id": me.id,
                "can_join_groups": me.can_join_groups,
                "can_read_all_group_messages": me.can_read_all_group_messages,
                "supports_inline_queries": me.supports_inline_queries,
                "webhook_url": self.settings.webhook_url,
                "webhook_port": (
                    self.settings.webhook_port if self.settings.webhook_url else None
                ),
            }
        except Exception as e:
            logger.error("Failed to get bot info", error=str(e))
            return {"status": "error", "error": str(e)}

    async def health_check(self) -> bool:
        """Perform health check."""
        try:
            if not self.app:
                return False

            # Try to get bot info
            await self.app.bot.get_me()
            return True
        except Exception as e:
            logger.error("Health check failed", error=str(e))
            return False

    async def _check_git_updates_on_startup(self) -> None:
        """Check git status and notify users about available updates."""
        import subprocess

        # Wait for bot to be fully initialized
        await asyncio.sleep(2)

        try:
            logger.info("Checking git status for updates")

            # Fetch latest changes from remote without merging
            subprocess.run(["git", "fetch"], capture_output=True, text=True, check=True)

            # Get current branch
            current_branch_result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True,
                text=True,
                check=True,
            )
            current_branch = current_branch_result.stdout.strip()

            # Check for unstaged changes
            status_result = subprocess.run(
                ["git", "status", "--porcelain"], capture_output=True, text=True
            )
            has_unstaged_changes = (
                bool(status_result.stdout.strip())
                if status_result.returncode == 0
                else False
            )

            # Get commit counts
            behind_result = subprocess.run(
                ["git", "rev-list", "--count", f"HEAD..origin/{current_branch}"],
                capture_output=True,
                text=True,
            )
            behind_count = (
                int(behind_result.stdout.strip())
                if behind_result.returncode == 0
                else 0
            )

            ahead_result = subprocess.run(
                ["git", "rev-list", "--count", f"origin/{current_branch}..HEAD"],
                capture_output=True,
                text=True,
            )
            ahead_count = (
                int(ahead_result.stdout.strip()) if ahead_result.returncode == 0 else 0
            )

            # Only send message if there's something actionable
            message = None

            if behind_count > 0 and ahead_count == 0:
                # Get latest commit info for updates
                log_result = subprocess.run(
                    ["git", "log", f"HEAD..origin/{current_branch}", "--oneline", "-5"],
                    capture_output=True,
                    text=True,
                )
                latest_commit_info = ""
                if log_result.returncode == 0 and log_result.stdout:
                    latest_commit_info = f"\n\n**Recent commits:**\n```\n{log_result.stdout.strip()}\n```"

                message = (
                    f"📥 **Update Available!**\n\n"
                    f"The bot is {behind_count} commit{'s' if behind_count != 1 else ''} behind origin/{current_branch}.\n"
                    f"Run `/self_update` to update the bot and get the latest features.{latest_commit_info}"
                )
            elif behind_count == 0 and ahead_count > 0:
                message = (
                    f"📤 Bot is {ahead_count} commit{'s' if ahead_count != 1 else ''} ahead of origin/{current_branch}.\n"
                    f"Consider pushing your changes."
                )
            elif behind_count > 0 and ahead_count > 0:
                # Get latest commit info for mixed status
                log_result = subprocess.run(
                    ["git", "log", f"HEAD..origin/{current_branch}", "--oneline", "-5"],
                    capture_output=True,
                    text=True,
                )
                latest_commit_info = ""
                if log_result.returncode == 0 and log_result.stdout:
                    latest_commit_info = f"\n\n**Recent commits:**\n```\n{log_result.stdout.strip()}\n```"

                message = (
                    f"🔄 **Repository Status:**\n"
                    f"• {behind_count} commit{'s' if behind_count != 1 else ''} behind origin/{current_branch}\n"
                    f"• {ahead_count} commit{'s' if ahead_count != 1 else ''} ahead of origin/{current_branch}\n\n"
                    f"Run `/self_update` to sync with the remote repository.{latest_commit_info}"
                )
            elif has_unstaged_changes:
                message = (
                    "📝 **Uncommitted Changes Detected**\n\n"
                    "The bot has uncommitted changes in the working directory.\n"
                    "Consider committing or stashing changes before running `/self_update`."
                )

            # Only send notification if there's an actionable message
            if message:
                for user_id in self.settings.allowed_users:
                    try:
                        await self.app.bot.send_message(
                            chat_id=user_id, text=message, parse_mode="Markdown"
                        )
                        logger.info("Sent startup notification", user_id=user_id)
                    except Exception as e:
                        logger.warning(
                            "Failed to send startup notification",
                            user_id=user_id,
                            error=str(e),
                        )
            else:
                logger.info("Bot is up to date, no startup notification needed")

        except subprocess.CalledProcessError as e:
            logger.error("Git command failed", error=str(e), stderr=e.stderr)
            # Send error notification
            error_message = (
                f"⚠️ **Git Status Check Failed**\n\n"
                f"Unable to check for updates. Git command failed.\n"
                f"Error: `{str(e)}`"
            )
            for user_id in self.settings.allowed_users:
                try:
                    await self.app.bot.send_message(
                        chat_id=user_id, text=error_message, parse_mode="Markdown"
                    )
                    logger.info("Sent git error notification", user_id=user_id)
                except Exception as send_error:
                    logger.warning(
                        "Failed to send git error notification",
                        user_id=user_id,
                        error=str(send_error),
                    )
        except Exception as e:
            logger.error("Failed to check git updates", error=str(e))
            # Send general error notification
            error_message = (
                f"⚠️ **Update Check Failed**\n\n"
                f"Unable to check for updates due to an unexpected error.\n"
                f"Error: `{str(e)}`"
            )
            for user_id in self.settings.allowed_users:
                try:
                    await self.app.bot.send_message(
                        chat_id=user_id, text=error_message, parse_mode="Markdown"
                    )
                    logger.info("Sent general error notification", user_id=user_id)
                except Exception as send_error:
                    logger.warning(
                        "Failed to send general error notification",
                        user_id=user_id,
                        error=str(send_error),
                    )
