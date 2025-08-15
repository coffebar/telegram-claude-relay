#!/usr/bin/env python3
"""Claude Code UserPromptSubmit hook handler."""

from hook_utils import handle_hook_event


def main():
    """Process UserPromptSubmit hook event via Unix socket."""
    handle_hook_event("UserPromptSubmit")


if __name__ == "__main__":
    main()
