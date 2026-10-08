"""Transition names select the same configuration and live database."""

import os


def setting(name, default=None):
    """Prefer SIMTASK settings; accept the equivalent legacy name when absent."""
    return os.environ.get("SIMTASK_" + name, os.environ.get("TASK_MCP_" + name, default))
