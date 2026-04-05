"""Disk-based cache for psxdata DataFrames using diskcache + parquet.

Cache location: ~/.psxdata/cache/ (configurable)
Serialisation: parquet via pyarrow — preserves dtypes across sessions
TTL: None (never expires) for historical data; CACHE_TTL_TODAY for today's data
"""
from __future__ import annotations

import io
import logging
from pathlib import Path

import diskcache
import pandas as pd

from psxdata.constants import CACHE_DIR
from psxdata.exceptions import CacheError

logger = logging.getLogger(__name__)


class DiskCache:
    """Persistent DataFrame cache backed by diskcache and parquet serialisation.

    Args:
        cache_dir: Path to the cache directory. Tilde is expanded.
            Defaults to CACHE_DIR (~/.psxdata/cache/).

    Key convention (caller's responsibility)::

        # Historical — never expires
        key = f"{symbol.upper()}_{start.isoformat()}_{end.isoformat()}"
        # Today's data — use CACHE_TTL_TODAY
        key = f"{symbol.upper()}_today"
    """

    def __init__(self, cache_dir: str = CACHE_DIR) -> None:
        resolved = Path(cache_dir).expanduser()
        resolved.mkdir(parents=True, exist_ok=True)
        try:
            self._cache = diskcache.Cache(str(resolved))
        except Exception as exc:
            raise CacheError(f"Failed to open cache at {resolved}") from exc

    def get(self, key: str) -> pd.DataFrame | None:
        """Retrieve a DataFrame by key.

        Args:
            key: Cache key string.

        Returns:
            DataFrame if found and not expired, None on miss.

        Raises:
            CacheError: On diskcache or parquet deserialisation failure.
        """
        try:
            raw = self._cache.get(key)
        except Exception as exc:
            raise CacheError(f"Cache read failed for key {key!r}") from exc
        if raw is None:
            return None
        try:
            return pd.read_parquet(io.BytesIO(raw))
        except Exception as exc:
            raise CacheError(f"Parquet deserialisation failed for key {key!r}") from exc

    def set(self, key: str, df: pd.DataFrame, ttl: int | None = None) -> None:
        """Store a DataFrame under key with optional TTL.

        Args:
            key: Cache key string.
            df: DataFrame to store.
            ttl: Time-to-live in seconds. None = never expires.

        Raises:
            CacheError: On serialisation or diskcache write failure.
        """
        try:
            buf = io.BytesIO()
            df.to_parquet(buf, index=True)
            raw = buf.getvalue()
        except Exception as exc:
            raise CacheError(f"Parquet serialisation failed for key {key!r}") from exc
        try:
            self._cache.set(key, raw, expire=ttl)
        except Exception as exc:
            raise CacheError(f"Cache write failed for key {key!r}") from exc
        logger.debug("Cache set: %r (%d bytes, ttl=%s)", key, len(raw), ttl)

    def delete(self, key: str) -> None:
        """Remove an entry by key. No-op if key doesn't exist."""
        try:
            self._cache.delete(key)
        except Exception as exc:
            raise CacheError(f"Cache delete failed for key {key!r}") from exc

    def clear(self) -> None:
        """Remove all entries from the cache."""
        try:
            self._cache.clear()
        except Exception as exc:
            raise CacheError("Cache clear failed") from exc
