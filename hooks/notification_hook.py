#!/usr/bin/env python3
"""Claude Code Notification hook handler using Unix sockets."""

from hook_utils import handle_hook_event


def main():
    """Process Notification hook event via Unix socket."""
    handle_hook_event("Notification")


if __name__ == "__main__":
    main()
