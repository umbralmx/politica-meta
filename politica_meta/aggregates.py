"""Report-equivalent aggregates derived from per-ad API data.

Core table: spend_by_page_region — one row per (page_id, region), built by
allocating each ad's spend interval across its delivery_by_region percentages.
All marginals (by page, by region, per-state top-N) derive from it and are
reconciled against directly-computed sums to catch allocation bugs.

Methodology invariants (see METODOLOGIA.md §5 and CONTEXT.md):
- Spend is ALWAYS an interval [spend_lower, spend_upper]; never a point estimate.
- An ad in Meta's open-ended top spend bucket has spend_upper = NULL. Decision
  (Jay, 2026-07-13): NULL propagates — any cell touched by such an ad reports
  spend_upper = NULL and upper_unbounded = True. Never cap upper at lower.
- delivery_by_region percentages are impression/reach share, not verified spend
  share: every allocated value is a MODELED interval (estimate_type =
  'region_allocated'), not an observed one.
- Percentages already sum to ~1.0 per ad including foreign regions (verified
  empirically, deviation < 0.02%). We do NOT renormalize after separating
  foreign regions; foreign-allocated spend lands in spend_by_region_nonmx.
"""

from __future__ import annotations

import json
import logging
import unicodedata
from pathlib import Path
from typing import Any

from .storage import AdStore

logger = logging.getLogger(__name__)

ESTIMATE_TYPE = "region_allocated"
TIME_METHOD = "start_month_cohort"

# --- Canonical Mexican states -------------------------------------------------

CANONICAL_MX_STATES = [
    "Aguascalientes", "Baja California", "Baja California Sur", "Campeche",
    "Chiapas", "Chihuahua", "Ciudad de México", "Coahuila", "Colima", "Durango",
    "Estado de México", "Guanajuato", "Guerrero", "Hidalgo", "Jalisco",
    "Michoacán", "Morelos", "Nayarit", "Nuevo León", "Oaxaca", "Puebla",
    "Querétaro", "Quintana Roo", "San Luis Potosí", "Sinaloa", "Sonora",
    "Tabasco", "Tamaulipas", "Tlaxcala", "Veracruz", "Yucatán", "Zacatecas",
]

# Variantes observadas en datos reales de la API (2026-07) más las previsibles.
_EXTRA_ALIASES = {
    "distrito federal": "Ciudad de México",
    "mexico city": "Ciudad de México",
    "state of mexico": "Estado de México",
    "michoacan de ocampo": "Michoacán",
    "coahuila de zaragoza": "Coahuila",
    "queretaro arteaga": "Querétaro",
    "queretaro de arteaga": "Querétaro",
    "veracruz de ignacio de la llave": "Veracruz",
}


def _norm(name: str) -> str:
    stripped = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return " ".join(stripped.lower().split())


_NORM_TO_CANONICAL = {_norm(s): s for s in CANONICAL_MX_STATES} | _EXTRA_ALIASES


def canonical_mx_region(name: str | None) -> str | None:
    """Canonical state name for the 32 entidades, or None if not a MX state."""
    if not name:
        return None
    return _NORM_TO_CANONICAL.get(_norm(name))


# --- Direct marginals (computed straight from ads; used for reconciliation) ----

# NULL-aware upper: si cualquier anuncio del grupo cae en el bucket abierto,
# el techo del grupo es desconocido (NULL) y upper_unbounded = 1.
_PAGE_QUERY = """
SELECT
    page_id,
    MAX(page_name) AS page_name,
    GROUP_CONCAT(DISTINCT bylines) AS bylines,
    COUNT(*) AS ads,
    SUM(spend_lower) AS spend_lower,
    CASE WHEN SUM(spend_upper IS NULL) > 0 THEN NULL ELSE SUM(spend_upper) END AS spend_upper,
    MAX(spend_upper IS NULL) AS upper_unbounded,
    SUM(impressions_lower) AS impressions_lower,
    CASE WHEN SUM(impressions_upper IS NULL) > 0 THEN NULL ELSE SUM(impressions_upper) END AS impressions_upper,
    MIN(ad_delivery_start_time) AS first_ad,
    MAX(ad_delivery_start_time) AS last_ad
FROM ads
WHERE (:start IS NULL OR ad_delivery_start_time >= :start)
  AND (:end IS NULL OR ad_delivery_start_time <= :end)
GROUP BY page_id
ORDER BY spend_lower DESC
"""

_MONTH_QUERY = """
SELECT
    substr(ad_delivery_start_time, 1, 7) AS month,
    COUNT(*) AS ads,
    SUM(spend_lower) AS spend_lower,
    CASE WHEN SUM(spend_upper IS NULL) > 0 THEN NULL ELSE SUM(spend_upper) END AS spend_upper,
    MAX(spend_upper IS NULL) AS upper_unbounded
FROM ads
WHERE ad_delivery_start_time IS NOT NULL
  AND (:start IS NULL OR ad_delivery_start_time >= :start)
  AND (:end IS NULL OR ad_delivery_start_time <= :end)
GROUP BY month
ORDER BY month
"""

_PAGE_MONTH_QUERY = """
SELECT
    page_id,
    MAX(page_name) AS page_name,
    substr(ad_delivery_start_time, 1, 7) AS month,
    COUNT(*) AS ads,
    SUM(spend_lower) AS spend_lower,
    CASE WHEN SUM(spend_upper IS NULL) > 0 THEN NULL ELSE SUM(spend_upper) END AS spend_upper,
    MAX(spend_upper IS NULL) AS upper_unbounded
FROM ads
WHERE ad_delivery_start_time IS NOT NULL
  AND (:start IS NULL OR ad_delivery_start_time >= :start)
  AND (:end IS NULL OR ad_delivery_start_time <= :end)
GROUP BY page_id, month
ORDER BY month, spend_lower DESC
"""

_REGION_ROWS_QUERY = """
SELECT page_id, page_name, bylines, spend_lower, spend_upper, delivery_by_region
FROM ads
WHERE delivery_by_region IS NOT NULL
  AND (:start IS NULL OR ad_delivery_start_time >= :start)
  AND (:end IS NULL OR ad_delivery_start_time <= :end)
"""

# Universo idéntico al de la asignación (solo anuncios CON delivery_by_region),
# para que la reconciliación compare peras con peras.
_PAGE_DIRECT_REGION_UNIVERSE_QUERY = """
SELECT page_id, SUM(spend_lower) AS spend_lower
FROM ads
WHERE delivery_by_region IS NOT NULL
  AND (:start IS NULL OR ad_delivery_start_time >= :start)
  AND (:end IS NULL OR ad_delivery_start_time <= :end)
GROUP BY page_id
"""


def spend_by_page(store: AdStore, start: str | None = None, end: str | None = None):
    import pandas as pd

    return pd.read_sql_query(_PAGE_QUERY, store.conn, params={"start": start, "end": end})


def spend_by_month(store: AdStore, start: str | None = None, end: str | None = None):
    import pandas as pd

    df = pd.read_sql_query(_MONTH_QUERY, store.conn, params={"start": start, "end": end})
    df["time_method"] = TIME_METHOD
    return df


def spend_by_page_month(store: AdStore, start: str | None = None, end: str | None = None):
    import pandas as pd

    df = pd.read_sql_query(_PAGE_MONTH_QUERY, store.conn, params={"start": start, "end": end})
    df["time_method"] = TIME_METHOD
    return df


# --- Per-ad detail (drives the advertiser drill-down in the dashboard) ----------

_AD_DETAIL_QUERY = """
SELECT id, page_id, page_name, bylines,
       substr(ad_delivery_start_time, 1, 10) AS start_date,
       spend_lower, spend_upper,
       creative_bodies, link_titles, delivery_by_region
FROM ads
WHERE (:start IS NULL OR ad_delivery_start_time >= :start)
  AND (:end IS NULL OR ad_delivery_start_time <= :end)
ORDER BY spend_lower DESC
"""

_SNIPPET_LEN = 200


def _first_text(json_list: str | None) -> str | None:
    """First non-empty string of a JSON-encoded list, truncated for display."""
    if not json_list:
        return None
    try:
        items = json.loads(json_list)
    except (TypeError, ValueError):
        return None
    for item in items if isinstance(items, list) else []:
        if isinstance(item, str) and item.strip():
            text = " ".join(item.split())
            return text[:_SNIPPET_LEN] + ("…" if len(text) > _SNIPPET_LEN else "")
    return None


def ad_detail(store: AdStore, start: str | None = None, end: str | None = None):
    """One row per ad, for the per-advertiser drill-down.

    - `regions_mx`: "Entidad:pct|Entidad:pct|…" (solo las 32 canónicas, orden
      descendente por porcentaje de entrega). Compacto para que la tabla quepa
      en un parquet publicable; el dashboard lo parsea al filtrar por entidad.
    - `ad_url`: vista pública facebook.com/ads/library/?id=<id>. Se excluye
      deliberadamente `ad_snapshot_url` porque incluye el access token.
    """
    import pandas as pd

    records = []
    cur = store.conn.execute(_AD_DETAIL_QUERY, {"start": start, "end": end})
    for ad_id, page_id, page_name, bylines, start_date, lo, hi, bodies, titles, region_json in cur:
        pairs: list[tuple[str, float]] = []
        if region_json:
            try:
                items = json.loads(region_json)
            except (TypeError, ValueError):
                items = []
            for item in items if isinstance(items, list) else []:
                canon = canonical_mx_region(item.get("region"))
                if canon is None:
                    continue
                try:
                    pairs.append((canon, float(item.get("percentage", 0))))
                except (TypeError, ValueError):
                    continue
            pairs.sort(key=lambda t: -t[1])
        records.append(
            {
                "ad_id": ad_id,
                "page_id": page_id,
                "page_name": page_name,
                "bylines": bylines,
                "start_date": start_date,
                "spend_lower": lo,
                "spend_upper": hi,
                "upper_unbounded": hi is None,
                "snippet": _first_text(bodies) or _first_text(titles),
                "regions_mx": "|".join(f"{n}:{p:.4f}" for n, p in pairs) or None,
                "ad_url": f"https://www.facebook.com/ads/library/?id={ad_id}",
            }
        )
    return pd.DataFrame(records)


# --- Core joint aggregate: page × region ---------------------------------------


def spend_by_page_region(
    store: AdStore, start: str | None = None, end: str | None = None
) -> tuple[Any, Any, dict[str, Any]]:
    """Allocate each ad's spend interval across its regions.

    Returns (mx_df, nonmx_df, diagnostics):
    - mx_df: rows for the 32 entidades (canonical names).
    - nonmx_df: same schema for foreign/Unknown regions (kept as a signal,
      out of the MX analysis tables). Region names stay as Meta sends them.
    """
    import pandas as pd

    cells: dict[tuple[str, str, bool], dict[str, Any]] = {}
    page_meta: dict[str, tuple[str | None, str | None]] = {}
    diagnostics = {
        "ads_allocated": 0,
        "ads_without_region": 0,
        "pct_sum_outliers": 0,  # anuncios cuya suma de percentages se desvía >1% de 1.0
        "malformed_region_items": 0,
    }

    cur = store.conn.execute(_REGION_ROWS_QUERY, {"start": start, "end": end})
    for page_id, page_name, bylines, spend_lower, spend_upper, region_json in cur:
        try:
            regions: list[dict[str, Any]] = json.loads(region_json)
        except (TypeError, ValueError):
            diagnostics["malformed_region_items"] += 1
            continue
        page_meta.setdefault(page_id, (page_name, bylines))
        pct_sum = 0.0
        for item in regions:
            raw_name = item.get("region")
            try:
                pct = float(item.get("percentage", 0))
            except (TypeError, ValueError):
                diagnostics["malformed_region_items"] += 1
                continue
            if not raw_name:
                diagnostics["malformed_region_items"] += 1
                continue
            pct_sum += pct
            canon = canonical_mx_region(raw_name)
            key = (page_id, canon or raw_name, canon is not None)
            acc = cells.setdefault(
                key,
                {"spend_lower": 0.0, "spend_upper": 0.0, "upper_unbounded": False, "ad_touches": 0},
            )
            acc["spend_lower"] += (spend_lower or 0.0) * pct
            if spend_upper is None:
                acc["upper_unbounded"] = True
            else:
                acc["spend_upper"] += spend_upper * pct
            acc["ad_touches"] += 1
        diagnostics["ads_allocated"] += 1
        if abs(pct_sum - 1.0) > 0.01:
            diagnostics["pct_sum_outliers"] += 1

    diagnostics["ads_without_region"] = store.conn.execute(
        """
        SELECT COUNT(*) FROM ads
        WHERE delivery_by_region IS NULL
          AND (:start IS NULL OR ad_delivery_start_time >= :start)
          AND (:end IS NULL OR ad_delivery_start_time <= :end)
        """,
        {"start": start, "end": end},
    ).fetchone()[0]

    def build(rows_mx: bool):
        records = []
        for (page_id, region, is_mx), acc in cells.items():
            if is_mx != rows_mx:
                continue
            name, byl = page_meta.get(page_id, (None, None))
            records.append(
                {
                    "page_id": page_id,
                    "page_name": name,
                    "bylines": byl,
                    "region": region,
                    "spend_lower": acc["spend_lower"],
                    "spend_upper": None if acc["upper_unbounded"] else acc["spend_upper"],
                    "upper_unbounded": acc["upper_unbounded"],
                    "ad_touches": acc["ad_touches"],
                    "estimate_type": ESTIMATE_TYPE,
                }
            )
        df = pd.DataFrame(records)
        if not df.empty:
            df = df.sort_values("spend_lower", ascending=False).reset_index(drop=True)
        return df

    return build(True), build(False), diagnostics


def spend_by_region(store: AdStore, start: str | None = None, end: str | None = None):
    """Region marginal (MX canonical), derived from the joint table."""
    mx, _, _ = spend_by_page_region(store, start, end)
    return _region_marginal(mx)


def _region_marginal(df):
    import pandas as pd

    if df.empty:
        return pd.DataFrame(columns=["region", "spend_lower", "spend_upper", "upper_unbounded", "ad_touches"])
    out = (
        df.groupby("region", as_index=False)
        .agg(
            spend_lower=("spend_lower", "sum"),
            spend_upper=("spend_upper", "sum"),
            upper_unbounded=("upper_unbounded", "max"),
            ad_touches=("ad_touches", "sum"),
        )
        .sort_values("spend_lower", ascending=False)
        .reset_index(drop=True)
    )
    out.loc[out["upper_unbounded"], "spend_upper"] = None
    return out


def top_pages_for_region(mx_df, region: str, top: int = 30):
    """Per-state page ranking (the chart previously only doable from Meta's CSV)."""
    canon = canonical_mx_region(region)
    if canon is None:
        raise ValueError(
            f"'{region}' no es una entidad federativa reconocida. "
            f"Opciones: {', '.join(CANONICAL_MX_STATES)}"
        )
    sub = mx_df[mx_df["region"] == canon]
    return canon, sub.sort_values("spend_lower", ascending=False).head(top).reset_index(drop=True)


# --- Reconciliation -------------------------------------------------------------


def reconcile(
    store: AdStore,
    mx_df,
    nonmx_df,
    start: str | None = None,
    end: str | None = None,
    rel_tol: float = 0.005,
) -> dict[str, Any]:
    """Check that allocation preserves totals (allocated ≈ direct sums over the
    same ad universe). A failure here means the allocation logic is wrong —
    catch it here, not in a published chart. Compares spend_lower (always
    defined); pages with unbounded uppers can't have their upper compared."""
    import pandas as pd

    allocated = pd.concat([mx_df, nonmx_df], ignore_index=True)
    result: dict[str, Any] = {"page_discrepancies": [], "total_direct": 0.0, "total_allocated": 0.0}
    if allocated.empty:
        return result

    alloc_pages = allocated.groupby("page_id")["spend_lower"].sum()
    direct = pd.read_sql_query(
        _PAGE_DIRECT_REGION_UNIVERSE_QUERY, store.conn, params={"start": start, "end": end}
    ).set_index("page_id")["spend_lower"]

    joined = pd.concat([alloc_pages.rename("allocated"), direct.rename("direct")], axis=1).fillna(0.0)
    result["total_allocated"] = float(joined["allocated"].sum())
    result["total_direct"] = float(joined["direct"].sum())
    for page_id, row in joined.iterrows():
        base = max(abs(row["direct"]), 1.0)
        if abs(row["allocated"] - row["direct"]) / base > rel_tol:
            result["page_discrepancies"].append(
                {"page_id": page_id, "direct": row["direct"], "allocated": row["allocated"]}
            )

    for d in result["page_discrepancies"][:20]:
        logger.warning(
            "Reconciliación: página %s directo=%.2f asignado=%.2f",
            d["page_id"], d["direct"], d["allocated"],
        )
    if result["page_discrepancies"]:
        logger.warning(
            "Reconciliación: %d páginas fuera de tolerancia (%.1f%%) — revisar asignación",
            len(result["page_discrepancies"]), rel_tol * 100,
        )
    else:
        logger.info(
            "Reconciliación OK: asignado %.2f vs directo %.2f (universo con región)",
            result["total_allocated"], result["total_direct"],
        )
    return result


# --- Output ----------------------------------------------------------------------


def _write(df, out_dir: Path, name: str) -> None:
    df.to_csv(out_dir / f"{name}.csv", index=False)
    df.to_parquet(out_dir / f"{name}.parquet", index=False)


def write_aggregates(
    store: AdStore,
    out_dir: str | Path,
    start: str | None = None,
    end: str | None = None,
    only: str | None = None,
) -> dict[str, int]:
    """Emit aggregate tables (CSV + Parquet). `only` limits to one family:
    page | region | page_region | month | page_month | ads. Default: all."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}

    need_joint = only in (None, "region", "page_region")
    if need_joint:
        mx, nonmx, diag = spend_by_page_region(store, start, end)
        logger.info(
            "Asignación: %d anuncios asignados, %d sin región (excluidos), "
            "%d con suma de porcentajes anómala, %d items malformados",
            diag["ads_allocated"], diag["ads_without_region"],
            diag["pct_sum_outliers"], diag["malformed_region_items"],
        )
        rec = reconcile(store, mx, nonmx, start, end)
        if only in (None, "page_region"):
            _write(mx, out, "spend_by_page_region")
            _write(nonmx, out, "spend_by_region_nonmx")
            counts["page_region"] = len(mx)
            counts["region_nonmx"] = len(nonmx)
        if only in (None, "region"):
            regions = _region_marginal(mx)
            _write(regions, out, "spend_by_region")
            counts["region"] = len(regions)
        counts["reconciliation_discrepancies"] = len(rec["page_discrepancies"])

    if only in (None, "page"):
        pages = spend_by_page(store, start, end)
        _write(pages, out, "spend_by_page")
        counts["page"] = len(pages)
    if only in (None, "month"):
        months = spend_by_month(store, start, end)
        _write(months, out, "spend_by_month")
        counts["month"] = len(months)
    if only in (None, "page_month"):
        pm = spend_by_page_month(store, start, end)
        _write(pm, out, "spend_by_page_month")
        counts["page_month"] = len(pm)
    if only in (None, "ads"):
        ads = ad_detail(store, start, end)
        _write(ads, out, "ad_detail")
        counts["ads"] = len(ads)
    if only in (None, "signals"):
        from .signals import page_signals  # import local: signals depende de este módulo

        sig = page_signals(store, start, end)
        _write(sig, out, "page_signals")
        counts["signals"] = len(sig)
    return counts
