"""Resumable date-windowed sweeps over the Ad Library.

Deep pagination on the ads_archive endpoint becomes unreliable past a few
thousand results (expired cursors, "reduce the amount of data" errors), so a
full-country download is partitioned into short delivery-date windows. Each
completed window is recorded; re-running the same command skips finished
windows and continues where it left off.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
from typing import Any, Iterator

from .client import AdLibraryClient
from .storage import AdStore

logger = logging.getLogger(__name__)

BATCH_SIZE = 500


def date_windows(start: str, end: str, days: int) -> Iterator[tuple[str, str]]:
    """Yield inclusive (min, max) YYYY-MM-DD windows covering [start, end]."""
    d0 = dt.date.fromisoformat(start)
    end_date = dt.date.fromisoformat(end)
    if d0 > end_date:
        raise ValueError(f"Fecha inicial {start} posterior a la final {end}")
    step = dt.timedelta(days=days)
    while d0 <= end_date:
        d1 = min(d0 + step - dt.timedelta(days=1), end_date)
        yield d0.isoformat(), d1.isoformat()
        d0 = d1 + dt.timedelta(days=1)


def query_key(query: dict[str, Any]) -> str:
    """Stable identifier of a query, so windows completed for one query
    don't shadow a different one (e.g. other search terms)."""
    canonical = json.dumps(query, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def run_sweep(
    client: AdLibraryClient,
    store: AdStore,
    *,
    start: str,
    end: str,
    window_days: int = 7,
    refresh: bool = False,
    **query: Any,
) -> int:
    """Download every ad delivered between start and end. Returns total ads seen."""
    key = query_key({"window_days": window_days, **query})
    total = 0
    windows = list(date_windows(start, end, window_days))
    for i, (dmin, dmax) in enumerate(windows, 1):
        if not refresh and store.window_done(key, dmin, dmax):
            logger.info("[%d/%d] %s → %s ya completada, se omite", i, len(windows), dmin, dmax)
            continue
        logger.info("[%d/%d] Descargando ventana %s → %s", i, len(windows), dmin, dmax)
        count = 0
        batch: list[dict[str, Any]] = []
        for ad in client.search(delivery_date_min=dmin, delivery_date_max=dmax, **query):
            batch.append(ad)
            count += 1
            if len(batch) >= BATCH_SIZE:
                store.upsert_many(batch)
                batch.clear()
                logger.info("  … %d anuncios en esta ventana", count)
        store.upsert_many(batch)
        store.mark_window_done(key, dmin, dmax, count)
        total += count
        logger.info("[%d/%d] Ventana %s → %s completa: %d anuncios", i, len(windows), dmin, dmax, count)
    return total
