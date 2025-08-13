"""Utility functions for hooks - no external dependencies."""

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