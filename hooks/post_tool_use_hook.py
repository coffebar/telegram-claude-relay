#!/usr/bin/env python3
"""Claude Code PostToolUse hook handler."""

from hook_utils import handle_hook_event


def main():
    """Process PostToolUse hook event via Unix socket."""
    handle_hook_event("PostToolUse")


if __name__ == "__main__":
    main()
