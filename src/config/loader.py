"""Simple configuration loading."""

import structlog

from src.exceptions import ConfigurationError

from .settings import Settings


logger = structlog.get_logger()


def load_config() -> Settings:
    """Load configuration from environment variables.

    Returns:
        Configured Settings instance

    Raises:
        ConfigurationError: If configuration is invalid
    """
    logger.info("Loading configuration from environment")

    try:
        # Load settings from environment variables
        settings = Settings()  # pydantic-settings reads from env automatically

        logger.info(
            "Configuration loaded successfully",
            debug=settings.debug,
        )

        return settings

    except Exception as e:
        logger.error("Failed to load configuration", error=str(e))
        raise ConfigurationError(f"Configuration loading failed: {e}") from e
