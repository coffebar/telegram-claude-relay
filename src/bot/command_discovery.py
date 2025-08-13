"""Dynamic command discovery from .claude/commands/ directories."""

from pathlib import Path
from typing import Dict, List, Optional, Set

import structlog


logger = structlog.get_logger()


class CommandDiscovery:
    """Discovers commands from global and project .claude/commands/ directories."""

    def __init__(self, project_cwd: Optional[str] = None):
        """Initialize command discovery.

        Args:
            project_cwd: Current working directory of the Claude process (for project commands)
        """
        self.project_cwd = project_cwd
        self._cached_commands: Optional[Dict[str, Dict[str, str]]] = None

    def set_project_cwd(self, project_cwd: str) -> None:
        """Update the project CWD and invalidate cache."""
        if self.project_cwd != project_cwd:
            self.project_cwd = project_cwd
            self._cached_commands = None
            logger.info("Project CWD updated", project_cwd=project_cwd)

    async def discover_commands(self) -> Dict[str, Dict[str, str]]:
        """Discover all available commands from global and project directories.

        Returns:
            Dict mapping command names to their metadata:
            {
                "command_name": {
                    "description": "Project command" | "Global command",
                    "source": "project" | "global",
                    "file_path": "/path/to/command.md"
                }
            }
        """
        if self._cached_commands is not None:
            return self._cached_commands

        commands = {}

        # Discover global commands
        global_commands = await self._discover_commands_in_directory(
            Path.home() / ".claude" / "commands",
            source="global",
            description="Global command",
        )
        commands.update(global_commands)

        # Discover project commands (if project CWD is available)
        if self.project_cwd:
            project_commands = await self._discover_commands_in_directory(
                Path(self.project_cwd) / ".claude" / "commands",
                source="project",
                description="Project command",
            )
            commands.update(project_commands)

        # Cache the results
        self._cached_commands = commands

        logger.info(
            "Command discovery completed",
            total_commands=len(commands),
            global_count=len([c for c in commands.values() if c["source"] == "global"]),
            project_count=len(
                [c for c in commands.values() if c["source"] == "project"]
            ),
            project_cwd=self.project_cwd,
            command_names=list(commands.keys()),
        )

        return commands

    async def _discover_commands_in_directory(
        self, commands_dir: Path, source: str, description: str
    ) -> Dict[str, Dict[str, str]]:
        """Discover commands in a specific directory.

        Args:
            commands_dir: Directory to search for command files
            source: Source type ("global" or "project")
            description: Description for commands from this source

        Returns:
            Dict of discovered commands
        """
        commands = {}

        try:
            if not commands_dir.exists() or not commands_dir.is_dir():
                logger.debug(
                    "Commands directory does not exist",
                    directory=str(commands_dir),
                    source=source,
                )
                return commands

            # Find all .md files in the directory
            md_files = list(commands_dir.glob("*.md"))

            for md_file in md_files:
                # Use filename (without .md extension) as command name
                command_name = md_file.stem

                # Skip files with invalid command names
                if not self._is_valid_command_name(command_name):
                    logger.warning(
                        "Skipping file with invalid command name",
                        file_path=str(md_file),
                        command_name=command_name,
                    )
                    continue

                commands[command_name] = {
                    "description": description,
                    "source": source,
                    "file_path": str(md_file),
                }

                logger.debug(
                    "Discovered command",
                    command_name=command_name,
                    source=source,
                    file_path=str(md_file),
                )

        except Exception as e:
            logger.error(
                "Error discovering commands in directory",
                directory=str(commands_dir),
                source=source,
                error=str(e),
            )

        return commands

    def _is_valid_command_name(self, name: str) -> bool:
        """Check if a command name is valid.

        Args:
            name: Command name to validate

        Returns:
            True if valid, False otherwise
        """
        # Command names should:
        # - Not be empty
        # - Not contain spaces or special characters that would break Telegram commands
        # - Not start with numbers
        # - Be reasonable length
        if not name:
            return False

        if len(name) > 50:  # Reasonable limit
            return False

        if name[0].isdigit():
            return False

        # Allow only alphanumeric and underscore (Telegram bot command requirement)
        # Hyphens are NOT allowed in Telegram bot commands
        allowed_chars = set(
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"
        )
        if not all(c in allowed_chars for c in name):
            return False

        return True

    def get_commands_for_menu(self) -> List[Dict[str, str]]:
        """Get commands formatted for Telegram bot menu.

        Returns:
            List of dicts with 'command' and 'description' keys
        """
        if self._cached_commands is None:
            return []

        menu_commands = []

        # Add built-in commands first
        menu_commands.extend(
            [
                {
                    "command": "clear",
                    "description": "Clear Claude's conversation history",
                },
                {"command": "compact", "description": "Compact Claude's conversation"},
            ]
        )

        # Add discovered commands
        for command_name, metadata in self._cached_commands.items():
            menu_commands.append(
                {"command": command_name, "description": metadata["description"]}
            )

        return menu_commands

    def invalidate_cache(self) -> None:
        """Invalidate the command cache to force rediscovery."""
        self._cached_commands = None
        logger.debug("Command cache invalidated")

    def get_all_command_names(self) -> Set[str]:
        """Get all available command names including built-ins.

        Returns:
            Set of all command names
        """
        command_names = {"clear", "compact"}  # Built-in commands

        if self._cached_commands:
            command_names.update(self._cached_commands.keys())

        return command_names
