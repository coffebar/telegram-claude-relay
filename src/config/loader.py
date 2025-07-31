"""Simple configuration loading."""

import os

from pathlib import Path
from typing import Optional

import structlog

from dotenv import load_dotenv

from src.exceptions import ConfigurationError

from .settings import Settings


logger = structlog.get_logger()


def load_config(
    env: Optional[str] = None, config_file: Optional[Path] = None
) -> Settings:
    """Load simple configuration from environment variables.

    Args:
        env: Environment name (ignored - kept for compatibility)
        config_file: Optional path to .env file

    Returns:
        Configured Settings instance

    Raises:
        ConfigurationError: If configuration is invalid
    """
    # Load .env file
    env_file = config_file or Path(".env")
    if env_file.exists():
        logger.info("Loading .env file", path=str(env_file))
        load_dotenv(env_file)
    else:
        logger.warning("No .env file found", path=str(env_file))

    # Simple environment detection
    env = env or os.getenv("ENVIRONMENT", "development")
    logger.info("Loading configuration", environment=env)

    try:
        # Load settings from environment variables
        settings = Settings()  # pydantic-settings reads from env automatically

        logger.info(
            "Configuration loaded successfully",
            environment=env,
            debug=settings.debug,
        )

        return settings

    except Exception as e:
        logger.error("Failed to load configuration", error=str(e), environment=env)
        raise ConfigurationError(f"Configuration loading failed: {e}") from e
