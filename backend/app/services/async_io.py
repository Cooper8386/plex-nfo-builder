"""Cancellation-safe ownership of filesystem and database worker calls."""
import asyncio
from collections.abc import Callable
from typing import TypeVar

_Result = TypeVar("_Result")


async def run_in_thread(operation: Callable[..., _Result], *args, **kwargs) -> _Result:
    """Drain a worker before cancellation can close resources it still uses."""
    task = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise
