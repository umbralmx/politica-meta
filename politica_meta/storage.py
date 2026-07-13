"""SQLite storage for scraped ads, with resumable sweep bookkeeping."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS ads (
    id TEXT PRIMARY KEY,
    page_id TEXT,
    page_name TEXT,
    bylines TEXT,
    currency TEXT,
    ad_creation_time TEXT,
    ad_delivery_start_time TEXT,
    ad_delivery_stop_time TEXT,
    spend_lower REAL,
    spend_upper REAL,
    impressions_lower REAL,
    impressions_upper REAL,
    audience_lower REAL,
    audience_upper REAL,
    creative_bodies TEXT,
    link_titles TEXT,
    link_captions TEXT,
    link_descriptions TEXT,
    languages TEXT,
    publisher_platforms TEXT,
    demographic_distribution TEXT,
    delivery_by_region TEXT,
    ad_snapshot_url TEXT,
    raw TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ads_page_id ON ads (page_id);
CREATE INDEX IF NOT EXISTS idx_ads_delivery_start ON ads (ad_delivery_start_time);

CREATE TABLE IF NOT EXISTS windows (
    query_key TEXT NOT NULL,
    date_min TEXT NOT NULL,
    date_max TEXT NOT NULL,
    ad_count INTEGER NOT NULL,
    completed_at TEXT NOT NULL,
    PRIMARY KEY (query_key, date_min, date_max)
);
"""


def range_bounds(value: Any) -> tuple[float | None, float | None]:
    """Extract (lower, upper) from an InsightsRangeValue like
    {"lower_bound": "100", "upper_bound": "199"}. Upper may be missing (">1M")."""
    if not isinstance(value, dict):
        return None, None

    def to_float(x: Any) -> float | None:
        try:
            return float(x)
        except (TypeError, ValueError):
            return None

    return to_float(value.get("lower_bound")), to_float(value.get("upper_bound"))


def _json_or_none(value: Any) -> str | None:
    return json.dumps(value, ensure_ascii=False) if value is not None else None


class AdStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def upsert_many(self, ads: Iterable[dict[str, Any]]) -> int:
        """Insert or refresh ads. Ads mutate while they deliver (spend ranges
        grow, stop time appears), so on conflict everything except first_seen
        is overwritten with the newest snapshot."""
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        rows = []
        for ad in ads:
            spend_lo, spend_hi = range_bounds(ad.get("spend"))
            impr_lo, impr_hi = range_bounds(ad.get("impressions"))
            aud_lo, aud_hi = range_bounds(ad.get("estimated_audience_size"))
            rows.append(
                (
                    ad.get("id"),
                    ad.get("page_id"),
                    ad.get("page_name"),
                    ad.get("bylines"),
                    ad.get("currency"),
                    ad.get("ad_creation_time"),
                    ad.get("ad_delivery_start_time"),
                    ad.get("ad_delivery_stop_time"),
                    spend_lo,
                    spend_hi,
                    impr_lo,
                    impr_hi,
                    aud_lo,
                    aud_hi,
                    _json_or_none(ad.get("ad_creative_bodies")),
                    _json_or_none(ad.get("ad_creative_link_titles")),
                    _json_or_none(ad.get("ad_creative_link_captions")),
                    _json_or_none(ad.get("ad_creative_link_descriptions")),
                    _json_or_none(ad.get("languages")),
                    _json_or_none(ad.get("publisher_platforms")),
                    _json_or_none(ad.get("demographic_distribution")),
                    _json_or_none(ad.get("delivery_by_region")),
                    ad.get("ad_snapshot_url"),
                    json.dumps(ad, ensure_ascii=False),
                    now,
                    now,
                )
            )
        if not rows:
            return 0
        with self.conn:
            self.conn.executemany(
                """
                INSERT INTO ads (
                    id, page_id, page_name, bylines, currency,
                    ad_creation_time, ad_delivery_start_time, ad_delivery_stop_time,
                    spend_lower, spend_upper, impressions_lower, impressions_upper,
                    audience_lower, audience_upper,
                    creative_bodies, link_titles, link_captions, link_descriptions,
                    languages, publisher_platforms, demographic_distribution,
                    delivery_by_region, ad_snapshot_url, raw, first_seen, last_seen
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    page_id=excluded.page_id,
                    page_name=excluded.page_name,
                    bylines=excluded.bylines,
                    currency=excluded.currency,
                    ad_creation_time=excluded.ad_creation_time,
                    ad_delivery_start_time=excluded.ad_delivery_start_time,
                    ad_delivery_stop_time=excluded.ad_delivery_stop_time,
                    spend_lower=excluded.spend_lower,
                    spend_upper=excluded.spend_upper,
                    impressions_lower=excluded.impressions_lower,
                    impressions_upper=excluded.impressions_upper,
                    audience_lower=excluded.audience_lower,
                    audience_upper=excluded.audience_upper,
                    creative_bodies=excluded.creative_bodies,
                    link_titles=excluded.link_titles,
                    link_captions=excluded.link_captions,
                    link_descriptions=excluded.link_descriptions,
                    languages=excluded.languages,
                    publisher_platforms=excluded.publisher_platforms,
                    demographic_distribution=excluded.demographic_distribution,
                    delivery_by_region=excluded.delivery_by_region,
                    ad_snapshot_url=excluded.ad_snapshot_url,
                    raw=excluded.raw,
                    last_seen=excluded.last_seen
                """,
                rows,
            )
        return len(rows)

    def window_done(self, query_key: str, date_min: str, date_max: str) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM windows WHERE query_key=? AND date_min=? AND date_max=?",
            (query_key, date_min, date_max),
        )
        return cur.fetchone() is not None

    def mark_window_done(self, query_key: str, date_min: str, date_max: str, ad_count: int) -> None:
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO windows (query_key, date_min, date_max, ad_count, completed_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(query_key, date_min, date_max) DO UPDATE SET
                    ad_count=excluded.ad_count, completed_at=excluded.completed_at
                """,
                (query_key, date_min, date_max, ad_count, now),
            )

    def stats(self) -> dict[str, Any]:
        cur = self.conn.execute(
            """
            SELECT COUNT(*), MIN(ad_delivery_start_time), MAX(ad_delivery_start_time),
                   SUM(spend_lower), SUM(spend_upper), COUNT(DISTINCT page_id)
            FROM ads
            """
        )
        total, dmin, dmax, spend_lo, spend_hi, pages = cur.fetchone()
        top_pages = self.conn.execute(
            """
            SELECT page_name, page_id, COUNT(*) AS ads,
                   SUM(spend_lower) AS spend_lower, SUM(spend_upper) AS spend_upper
            FROM ads GROUP BY page_id ORDER BY spend_upper DESC LIMIT 15
            """
        ).fetchall()
        return {
            "total_ads": total,
            "delivery_from": dmin,
            "delivery_to": dmax,
            "spend_lower_sum": spend_lo,
            "spend_upper_sum": spend_hi,
            "distinct_pages": pages,
            "top_pages": top_pages,
        }
