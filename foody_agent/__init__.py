"""
FOODY Google ADK Package Entrypoint.
Exports `root_agent` for `adk web` and `adk run`.
"""

from . import agent
from .agent import root_agent

__all__ = ["agent", "root_agent"]

