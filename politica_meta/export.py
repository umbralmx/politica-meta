"""Export the SQLite database to flat files for analysis / Streamlit."""

from __future__ import annotations

from pathlib import Path

from .storage import AdStore

EXPORT_QUERY = """
SELECT
    id, page_id, page_name, bylines, currency,
    ad_creation_time, ad_delivery_start_time, ad_delivery_stop_time,
    spend_lower, spend_upper,
    (spend_lower + COALESCE(spend_upper, spend_lower)) / 2.0 AS spend_mid,
    impressions_lower, impressions_upper,
    audience_lower, audience_upper,
    creative_bodies, link_titles, link_captions, link_descriptions,
    languages, publisher_platforms,
    demographic_distribution, delivery_by_region,
    ad_snapshot_url, first_seen, last_seen
FROM ads
"""


def export(store: AdStore, out_path: str | Path) -> int:
    """Write ads to CSV or Parquet depending on the file extension."""
    import pandas as pd

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    df = pd.read_sql_query(EXPORT_QUERY, store.conn)
    # ad_snapshot_url incluye el access token del scraper: nunca debe salir
    # en un archivo compartible.
    df["ad_snapshot_url"] = df["ad_snapshot_url"].str.replace(
        r"access_token=[^&]+", "access_token=REDACTED", regex=True
    )
    suffix = out.suffix.lower()
    if suffix == ".csv":
        df.to_csv(out, index=False)
    elif suffix == ".parquet":
        df.to_parquet(out, index=False)
    else:
        raise ValueError(f"Formato no soportado: {suffix} (usa .csv o .parquet)")
    return len(df)
