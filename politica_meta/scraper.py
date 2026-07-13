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

from .client import AdLibraryClient, AdLibraryError
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


def _download_window(
    client: AdLibraryClient,
    store: AdStore,
    dmin: str,
    dmax: str,
    query: dict[str, Any],
) -> int:
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
    return count


def _process_window(
    client: AdLibraryClient,
    store: AdStore,
    key: str,
    dmin: str,
    dmax: str,
    query: dict[str, Any],
    refresh: bool,
    failed: list[tuple[str, str, str]],
) -> int:
    """Download one window; on persistent API failure (deep pagination breaks on
    big result sets) split it in half and retry each side. A 1-day window that
    still fails is recorded and the sweep continues."""
    if not refresh and store.window_done(key, dmin, dmax):
        logger.info("Ventana %s → %s ya completada, se omite", dmin, dmax)
        return 0
    try:
        count = _download_window(client, store, dmin, dmax, query)
    except AdLibraryError as exc:
        d0, d1 = dt.date.fromisoformat(dmin), dt.date.fromisoformat(dmax)
        span = (d1 - d0).days + 1
        if span <= 1:
            logger.error("Ventana %s → %s falló definitivamente: %s", dmin, dmax, exc)
            failed.append((dmin, dmax, str(exc)))
            return 0
        mid = d0 + dt.timedelta(days=span // 2 - 1)
        logger.warning(
            "Ventana %s → %s falló (%s); dividiendo en %s→%s y %s→%s",
            dmin, dmax, exc, dmin, mid.isoformat(), (mid + dt.timedelta(days=1)).isoformat(), dmax,
        )
        count = _process_window(client, store, key, dmin, mid.isoformat(), query, refresh, failed)
        count += _process_window(
            client, store, key, (mid + dt.timedelta(days=1)).isoformat(), dmax, query, refresh, failed
        )
        # Las dos mitades quedaron registradas; marcar también la ventana madre
        # para que una reanudación futura no la reintente completa.
        prior_failures = any(dmin <= f[0] and f[1] <= dmax for f in failed)
        if not prior_failures:
            store.mark_window_done(key, dmin, dmax, count)
        return count
    store.mark_window_done(key, dmin, dmax, count)
    return count


def run_sweep(
    client: AdLibraryClient,
    store: AdStore,
    *,
    start: str,
    end: str,
    window_days: int = 7,
    refresh: bool = False,
    **query: Any,
) -> tuple[int, list[tuple[str, str, str]]]:
    """Download every ad delivered between start and end.

    Returns (total ads seen, failed windows). A window that fails even after
    being split down to single days is skipped and reported, not fatal."""
    key = query_key({"window_days": window_days, **query})
    total = 0
    failed: list[tuple[str, str, str]] = []
    windows = list(date_windows(start, end, window_days))
    for i, (dmin, dmax) in enumerate(windows, 1):
        logger.info("[%d/%d] Ventana %s → %s", i, len(windows), dmin, dmax)
        count = _process_window(client, store, key, dmin, dmax, query, refresh, failed)
        total += count
        if count:
            logger.info("[%d/%d] Ventana %s → %s completa: %d anuncios", i, len(windows), dmin, dmax, count)
    return total, failed
