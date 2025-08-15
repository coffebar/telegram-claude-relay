#!/usr/bin/env python3
"""Claude Code Stop hook handler using Unix sockets."""

from hook_utils import handle_hook_event


def main():
    """Process Stop hook event via Unix socket."""
    handle_hook_event("Stop")


if __name__ == "__main__":
    main()
