#!/usr/bin/env python3
"""Claude Code PreToolUse hook handler."""

from hook_utils import handle_hook_event


def main():
    """Process PreToolUse hook event via Unix socket."""
    handle_hook_event("PreToolUse")


if __name__ == "__main__":
    main()
