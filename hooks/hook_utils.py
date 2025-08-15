"""Utility functions for hooks - no external dependencies."""

import json
import socket
import sys

from pathlib import Path


def get_socket_for_project(cwd):
    """Get the socket name for a project based on its CWD.

    Args:
        cwd: Current working directory from Claude

    Returns:
        Socket filename to use
    """
    if not cwd:
        return "telegram-relay.sock"

    # Extract project name from CWD (last directory component)
    project_name = Path(cwd).name

    # Generate socket name based on project
    socket_name = f"telegram-relay-{project_name}.sock"

    # Check if socket exists
    socket_path = Path(__file__).parent.parent / socket_name
    if socket_path.exists():
        return socket_name

    # Fallback to default if project-specific socket doesn't exist
    return "telegram-relay.sock"


def handle_hook_event(hook_event_name, default_response=None):
    """Common hook handler that immediately allows continuation and optionally sends notifications.

    Args:
        hook_event_name: Name of the hook event (e.g., "PreToolUse", "PostToolUse")
        default_response: Default response to send immediately (defaults to {"continue": True})
    """
    # Immediately allow continuation to prevent blocking Claude
    if default_response is None:
        default_response = {"continue": True}

    print(json.dumps(default_response))
    sys.stdout.flush()

    try:
        # Read hook input from stdin
        hook_input = json.loads(sys.stdin.read())

        # Get CWD from hook data to determine which socket to use
        cwd = hook_input.get("cwd", "")
        socket_name = get_socket_for_project(cwd)
        socket_path = Path(__file__).parent.parent / socket_name

        # Add hook type for identification
        hook_input["hook_event_name"] = hook_event_name

        # Check if socket exists
        if not socket_path.exists():
            # Bot not running, nothing more to do
            sys.exit(0)

        # Connect to Unix socket for notification purposes
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(str(socket_path))

            # Send data for notification
            sock.sendall(json.dumps(hook_input).encode("utf-8"))

            # No need to wait for response since we already allowed continuation
            sys.exit(0)

        finally:
            sock.close()

    except Exception:
        # Already allowed continuation, so just exit silently on any errors
        sys.exit(0)
