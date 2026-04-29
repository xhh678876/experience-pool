"""Standalone experience pool: SQLite + filesystem + in-process vectors.

Same data model and pipeline order as the FastAPI / docker version, just with
embedded backends so the loop runs on a laptop with no external services.
"""

from .pool import ExperiencePool, PoolConfig

__all__ = ["ExperiencePool", "PoolConfig"]
