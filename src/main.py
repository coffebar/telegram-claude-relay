"""Main entry point for Claude Code Telegram Bot."""

import argparse
import asyncio
import atexit
import logging
import signal
import sys

from pathlib import Path
from typing import Any, Dict

import structlog

from src import __version__
from src.bot.core import ClaudeTelegramBot
from src.claude import ClaudeIntegration
from src.config.settings import Settings
from src.exceptions import ConfigurationError
from src.security.auth import AuthenticationManager, WhitelistAuthProvider
from src.security.rate_limiter import RateLimiter


def setup_logging(
    debug: bool = False, log_file: str = "telegram-claude-bot.log"
) -> None:
    """Configure structured logging with both console and file output."""
    level = logging.DEBUG if debug else logging.INFO

    # Configure standard logging with both console and file handlers
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Clear existing handlers
    root_logger.handlers = []

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger.addHandler(console_handler)

    # File handler with rotation
    from logging.handlers import RotatingFileHandler

    file_handler = RotatingFileHandler(
        filename=log_file,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger.addHandler(file_handler)

    # Configure structlog
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            (
                structlog.processors.JSONRenderer()
                if not debug
                else structlog.dev.ConsoleRenderer()
            ),
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Claude Code Telegram Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--version", action="version", version=f"Claude Code Telegram Bot {__version__}"
    )

    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    parser.add_argument("--config-file", type=Path, help="Path to configuration file")

    return parser.parse_args()


async def create_application(config: Settings) -> Dict[str, Any]:
    """Create and configure the application components."""
    logger = structlog.get_logger()
    logger.info("Creating application components")

    if not config.allowed_users:
        raise ConfigurationError("ALLOWED_USERS must be configured for security")

    auth_manager = AuthenticationManager([WhitelistAuthProvider(config.allowed_users)])
    rate_limiter = RateLimiter(config)

    logger.info("Using tmux integration only")
    claude_integration = ClaudeIntegration(
        config=config,
    )

    logger.info("tmux integration ready", pane=config.pane or "auto-discovery")

    # Create bot with all dependencies
    dependencies = {
        "auth_manager": auth_manager,
        "rate_limiter": rate_limiter,
        "claude_integration": claude_integration,
    }

    bot = ClaudeTelegramBot(config, dependencies)

    logger.info("Application components created successfully")

    return {
        "bot": bot,
        "claude_integration": claude_integration,
        "config": config,
    }


async def run_application(app: Dict[str, Any]) -> int:
    """Run the application with graceful shutdown handling.

    Returns:
        Exit code: 0 for normal exit, 42 for restart request
    """
    logger = structlog.get_logger()
    bot: ClaudeTelegramBot = app["bot"]
    claude_integration: ClaudeIntegration = app["claude_integration"]
    config: Settings = app["config"]

    # Set up signal handlers for graceful shutdown
    shutdown_event = asyncio.Event()
    restart_requested = False

    def signal_handler(signum, frame):
        logger.info("Shutdown signal received", signal=signum)
        shutdown_event.set()

    def restart_handler(signum, frame):
        nonlocal restart_requested
        logger.info("Restart signal received (SIGUSR1), requesting restart")
        restart_requested = True
        shutdown_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGHUP, signal_handler)  # Handle terminal hangup
    signal.signal(signal.SIGUSR1, restart_handler)  # Handle restart request

    # Initialize socket server reference for cleanup
    socket_server = None

    # Get project CWD and generate socket name based on project
    project_cwd = None
    project_name = None
    if claude_integration and hasattr(claude_integration, "tmux_integration"):
        await claude_integration._ensure_tmux_integration()
        if claude_integration.tmux_integration and hasattr(
            claude_integration.tmux_integration, "tmux_client"
        ):
            try:
                project_cwd = (
                    await claude_integration.tmux_integration.tmux_client.get_pane_cwd()
                )
                # Extract project name from CWD (last directory component)
                project_name = Path(project_cwd).name
                # Update socket path to use project name
                config.socket_path = f"telegram-relay-{project_name}.sock"
                logger.info(
                    "Generated project-based socket name",
                    cwd=project_cwd,
                    project=project_name,
                    socket=config.socket_path,
                )
            except Exception as e:
                logger.warning(
                    "Could not get project CWD, using default socket", error=str(e)
                )

    socket_path = Path.cwd() / config.socket_path

    # Register cleanup function for socket file
    def cleanup_socket():
        if socket_path.exists():
            try:
                socket_path.unlink()
                logger.info("Cleaned up socket file via atexit")
            except Exception as e:
                logger.error("Failed to cleanup socket file", error=str(e))

    atexit.register(cleanup_socket)

    try:
        # Start the bot
        logger.info("Starting Claude Code Telegram Bot")

        # Start Unix socket server (required for Claude response monitoring)
        socket_task = None
        webhook_handler = None

        logger.info("Preparing Unix socket server for Claude conversation monitoring")
        from src.bot.handlers.webhook import ConversationWebhookHandler
        from src.claude.conversation_monitor import ConversationMonitor
        from src.claude.unix_socket_server import UnixSocketServer

        # Run bot in background task
        bot_task = asyncio.create_task(bot.start())
        shutdown_task = asyncio.create_task(shutdown_event.wait())

        # Wait a moment for bot initialization
        await asyncio.sleep(0.5)

        # Start the socket server (required for Claude response handling)
        if bot.app and bot.app.bot:
            # Create webhook handler and monitor
            webhook_handler = ConversationWebhookHandler(bot.app.bot, config)

            # Add webhook handler to bot dependencies
            bot.app.bot_data["webhook_handler"] = webhook_handler
            bot.app.bot_data["message_sender"] = webhook_handler.message_sender

            # Initialize subscriptions for all allowed users
            await webhook_handler.initialize_subscriptions()

            monitor = ConversationMonitor(
                config, webhook_handler.handle_conversation_update
            )

            # Start Unix socket server
            socket_server = UnixSocketServer(config, monitor)

            # Ensure tmux integration is initialized and pass client reference
            await claude_integration._ensure_tmux_integration()
            if (
                hasattr(claude_integration, "tmux_integration")
                and claude_integration.tmux_integration
            ):
                if hasattr(claude_integration.tmux_integration, "tmux_client"):
                    socket_server.set_tmux_client(
                        claude_integration.tmux_integration.tmux_client
                    )
                    logger.info("Set tmux client reference for CWD filtering")

            socket_task = asyncio.create_task(socket_server.start())
            logger.info("Unix socket server started")

        # Wait for either bot completion or shutdown signal
        tasks = [bot_task, shutdown_task]
        if socket_task:
            tasks.append(socket_task)

        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

        # Cancel remaining tasks
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    except Exception as e:
        logger.error("Application error", error=str(e))
        raise
    finally:
        # Graceful shutdown
        logger.info("Shutting down application")

        try:
            await bot.stop()
            await claude_integration.shutdown()

            # Clean up socket server
            if socket_server:
                logger.info("Stopping Unix socket server")
                await socket_server.stop()

            # No route cleanup needed - using project-based auto-discovery

        except Exception as e:
            logger.error("Error during shutdown", error=str(e))

        logger.info("Application shutdown complete")

    # Return exit code based on restart request
    if restart_requested:
        logger.info("Returning exit code 42 for restart")
        return 42
    return 0


async def main() -> None:
    """Main application entry point."""
    args = parse_args()

    # Load config first to get session info for log file naming
    from src.config import load_config

    config = load_config(config_file=args.config_file)

    # Setup logging with file output using session ID from pane
    if config.pane:
        session_id = config.pane.split(":")[0]
        log_file = f"telegram-claude-bot-session-{session_id}.log"
    else:
        log_file = "telegram-claude-bot.log"
    setup_logging(debug=args.debug or config.debug, log_file=log_file)

    logger = structlog.get_logger()
    logger.info("=" * 80)
    logger.info(
        "Starting Claude Code Telegram Bot", version=__version__, log_file=log_file
    )

    try:

        logger.info(
            "Configuration loaded",
            environment="production" if config.is_production else "development",
            debug=config.debug,
            allowed_users=config.allowed_users,
            tmux_pane=config.pane or "auto-discovery",
        )

        # Initialize bot and Claude integration
        app = await create_application(config)
        exit_code = await run_application(app)

        # Exit with the appropriate code
        if exit_code != 0:
            sys.exit(exit_code)

    except ConfigurationError as e:
        logger.error("Configuration error", error=str(e))
        sys.exit(1)
    except Exception as e:
        logger.exception("Unexpected error", error=str(e))
        sys.exit(1)


def run() -> None:
    """Synchronous entry point for setuptools."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutdown requested by user")
        sys.exit(0)


if __name__ == "__main__":
    run()
