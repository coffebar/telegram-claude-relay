#!/usr/bin/env python3
"""Manage Claude Code hooks installation and uninstallation."""

import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


class HookManager:
    """Manages Claude Code hooks installation and uninstallation."""

    def __init__(self):
        # Determine paths
        self.script_dir = Path(__file__).parent.absolute()
        self.project_root = self.script_dir.parent
        self.hooks_dir = self.project_root / "hooks"
        self.template_path = self.project_root / "claude-code-settings.json"

        # Claude settings path
        self.claude_dir = Path.home() / ".claude"
        self.settings_path = self.claude_dir / "settings.json"

        # Get home directory for path conversion
        self.home = os.environ.get("HOME", str(Path.home()))

    def _convert_to_tilde_path(self, path: str) -> str:
        """Convert absolute path to ~/ format if it starts with home directory."""
        if path.startswith(self.home):
            return path.replace(self.home, "~", 1)
        return path

    def _backup_settings(self) -> Path:
        """Create a timestamped backup of current settings."""
        if not self.settings_path.exists():
            print(f"No existing settings at {self.settings_path}")
            return None

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = self.settings_path.with_suffix(
            f".json.backup.{timestamp}")

        shutil.copy2(self.settings_path, backup_path)
        print(f"✅ Created backup: {backup_path}")
        return backup_path

    def _load_settings(self) -> Dict[str, Any]:
        """Load existing settings or create new structure."""
        if self.settings_path.exists():
            with open(self.settings_path, 'r') as f:
                return json.load(f)
        else:
            print(
                f"No existing settings found. Creating new settings at {self.settings_path}")
            return {}

    def _save_settings(self, settings: Dict[str, Any]) -> None:
        """Save settings to file."""
        # Ensure .claude directory exists
        self.claude_dir.mkdir(exist_ok=True)

        with open(self.settings_path, 'w') as f:
            json.dump(settings, f, indent=2)
        print(f"✅ Saved settings to {self.settings_path}")

    def _load_template_hooks(self) -> Dict[str, List[Dict[str, Any]]]:
        """Load hook definitions from template."""
        if not self.template_path.exists():
            print(f"❌ Template not found: {self.template_path}")
            sys.exit(1)

        with open(self.template_path, 'r') as f:
            template = json.load(f)

        return template.get("hooks", {})

    def _create_hook_entry(self, hook_filename: str) -> Dict[str, Any]:
        """Create a hook entry with proper path."""
        hook_path = self.hooks_dir / hook_filename

        # Verify hook exists
        if not hook_path.exists():
            print(f"⚠️  Warning: Hook script not found: {hook_path}")

        # Convert to ~/ format
        hook_path_str = self._convert_to_tilde_path(str(hook_path))

        return {
            "hooks": [
                {
                    "type": "command",
                    "command": hook_path_str
                }
            ]
        }

    def install(self) -> None:
        """Install hooks into Claude settings."""
        print("🔧 Installing Claude Code hooks...")

        # Backup existing settings
        self._backup_settings()

        # Load current settings
        settings = self._load_settings()

        # Ensure hooks section exists
        if "hooks" not in settings:
            settings["hooks"] = {}

        # Load template hooks
        template_hooks = self._load_template_hooks()

        # Track what we're adding
        added_hooks = []

        # Process each hook type
        for hook_type, hook_configs in template_hooks.items():
            # Ensure hook type list exists
            if hook_type not in settings["hooks"]:
                settings["hooks"][hook_type] = []

            # Extract hook filename from template
            for config in hook_configs:
                for hook in config.get("hooks", []):
                    command = hook.get("command", "")
                    # Extract filename from template path
                    hook_filename = os.path.basename(command)

                    # Create hook entry with resolved path
                    new_hook_entry = self._create_hook_entry(hook_filename)

                    # Check if this exact hook is already present
                    hook_command = new_hook_entry["hooks"][0]["command"]
                    already_exists = any(
                        hook_entry.get("hooks", [{}])[0].get(
                            "command") == hook_command
                        for hook_entry in settings["hooks"][hook_type]
                    )

                    if not already_exists:
                        settings["hooks"][hook_type].append(new_hook_entry)
                        added_hooks.append(f"{hook_type}: {hook_filename}")
                        print(f"  ✅ Added {hook_type} hook: {hook_command}")
                    else:
                        print(
                            f"  ⏭️  Skipped {hook_type} hook (already exists): {hook_filename}")

        # Save updated settings
        self._save_settings(settings)

        # Make hook scripts executable
        self._ensure_hooks_executable()

        if added_hooks:
            print(f"\n✅ Successfully installed {len(added_hooks)} hooks")
        else:
            print("\n✅ All hooks were already installed")

    def uninstall(self) -> None:
        """Uninstall hooks from Claude settings."""
        print("🔧 Uninstalling Claude Code hooks...")

        # Look for most recent backup
        backups = sorted(self.claude_dir.glob("settings.json.backup.*"))

        if backups:
            # Restore from most recent backup
            latest_backup = backups[-1]
            print(f"📂 Restoring from backup: {latest_backup}")
            shutil.copy2(latest_backup, self.settings_path)
            print("✅ Settings restored from backup")
        else:
            # No backup - remove our hooks manually
            print("No backup found. Removing hooks manually...")

            settings = self._load_settings()
            if "hooks" not in settings:
                print("No hooks section found in settings")
                return

            # Get our hook paths
            our_hooks = set()
            template_hooks = self._load_template_hooks()

            for hook_type, hook_configs in template_hooks.items():
                for config in hook_configs:
                    for hook in config.get("hooks", []):
                        command = hook.get("command", "")
                        hook_filename = os.path.basename(command)
                        hook_path = self.hooks_dir / hook_filename
                        tilde_path = self._convert_to_tilde_path(
                            str(hook_path))
                        our_hooks.add(tilde_path)

            # Remove our hooks from each hook type
            removed_count = 0
            for hook_type, hook_list in settings["hooks"].items():
                original_count = len(hook_list)
                settings["hooks"][hook_type] = [
                    entry for entry in hook_list
                    if not any(
                        hook.get("command") in our_hooks
                        for hook in entry.get("hooks", [])
                    )
                ]
                removed = original_count - len(settings["hooks"][hook_type])
                if removed > 0:
                    removed_count += removed
                    print(f"  ✅ Removed {removed} {hook_type} hook(s)")

            # Clean up empty hook types
            settings["hooks"] = {
                k: v for k, v in settings["hooks"].items() if v
            }

            # Save updated settings
            self._save_settings(settings)
            print(f"\n✅ Successfully removed {removed_count} hooks")

    def _ensure_hooks_executable(self) -> None:
        """Ensure all hook scripts are executable."""
        hook_files = self.hooks_dir.glob("*.py")
        for hook_file in hook_files:
            os.chmod(hook_file, 0o755)
        print("✅ Made all hook scripts executable")


def main():
    """Main entry point."""
    if len(sys.argv) != 2 or sys.argv[1] not in ["install", "uninstall"]:
        print("Usage: manage_hooks.py [install|uninstall]")
        sys.exit(1)

    manager = HookManager()

    if sys.argv[1] == "install":
        manager.install()
    else:
        manager.uninstall()


if __name__ == "__main__":
    main()