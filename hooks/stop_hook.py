#!/usr/bin/env python3
"""Claude Code Stop hook handler using Unix sockets."""

import json
import socket
import sys

from pathlib import Path


def main():
    """Process Stop hook event via Unix socket."""
    # Socket is in the project root directory (parent of hooks directory)
    socket_path = Path(__file__).parent.parent / "telegram-relay.sock"

    try:
        # Read hook input from stdin
        hook_input = json.loads(sys.stdin.read())

        # Check if socket exists
        if not socket_path.exists():
            # Bot not running, return success to avoid blocking Claude
            print(json.dumps({"status": "ok", "continue": True}))
            sys.exit(0)

        # Connect to Unix socket
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(str(socket_path))

            # Send data
            sock.sendall(json.dumps(hook_input).encode("utf-8"))

            # Receive response
            response_data = sock.recv(4096)
            response = json.loads(response_data.decode("utf-8"))

            # Output response
            print(json.dumps(response))
            sys.exit(0)

        finally:
            sock.close()

    except Exception as e:
        # Don't block Claude on errors
        error_result = {
            "status": "ok",  # Return ok to not block Claude
            "message": str(e),
            "continue": True,
        }
        print(json.dumps(error_result))
        sys.exit(0)


if __name__ == "__main__":
    main()
