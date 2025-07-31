"""tmux integration exceptions."""


class TmuxError(Exception):
    """Base tmux integration error."""


class TmuxPaneNotFoundError(TmuxError):
    """tmux pane not found or not accessible."""


class TmuxResponseTimeoutError(TmuxError):
    """Timeout waiting for Claude response."""


class TmuxCommandError(TmuxError):
    """Error executing tmux command."""
