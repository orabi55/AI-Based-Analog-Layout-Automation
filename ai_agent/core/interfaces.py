"""
Core Interfaces
===============
Standard result type and decorator for all layout tool functions.

Usage:
    from ai_agent.core.interfaces import LayoutToolResult, wrap_tool

    @wrap_tool
    def my_operation(nodes, ...) -> LayoutToolResult:
        ...
        return LayoutToolResult(success=True, message="done", changed=True,
                                nodes=nodes, metrics={}, warnings=[])
"""

from __future__ import annotations

import functools
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, List

logger = logging.getLogger("ai_agent")


@dataclass
class LayoutToolResult:
    """Uniform return type for all layout tool operations."""
    success: bool
    message: str
    changed: bool = False
    nodes: List[Any] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


def wrap_tool(fn: Callable) -> Callable:
    """Decorator that makes any layout operation safe to call.

    Guarantees:
    - Never raises — all exceptions are caught and returned as a failed LayoutToolResult.
    - Logs every caught exception at ERROR level via the ai_agent logger.
    - Preserves the original function's name and docstring.
    """
    @functools.wraps(fn)
    def _wrapper(*args, **kwargs) -> LayoutToolResult:
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            logger.error("[wrap_tool] %s raised %s: %s", fn.__qualname__, type(exc).__name__, exc, exc_info=True)
            return LayoutToolResult(
                success=False,
                message=str(exc),
                changed=False,
                nodes=[],
                metrics={},
                warnings=[],
            )
    return _wrapper
