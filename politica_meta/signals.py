"""Señales de propaganda encubierta por página (metodología §6).

Cada señal es un HECHO derivable de los datos de la API, no un veredicto:
la tabla publica señales documentadas y deja la inferencia al análisis
editorial. Deliberadamente NO se calcula un "score de encubrimiento"
compuesto: sin un conjunto validado de casos confirmados, cualquier
ponderación sería arbitraria e indefendible.

Señales v1 (todas por página):
- pct_sin_pagador: proporción de anuncios sin "Pagado por" (bylines vacío).
  Los anuncios políticos en MX requieren disclaimer; correr sin él es la
  señal más directa.
- pagadores_distintos: nº de bylines distintos no vacíos. Una página que
  cobra pauta de muchos pagadores actúa como intermediaria.
- pct_pagador_ajeno: proporción de anuncios cuyo pagador declarado no
  coincide con el nombre de la página (normalizados). Página que pauta por
  cuenta de terceros.
- perfil_de_medio: el nombre de la página sugiere medio informativo
  (regex conservador). Relevante porque el modus operandi documentado es
  rutear pauta política a través de "noticieros" locales.
- pct_entrega_extranjera: parte del gasto (cota inferior) entregada fuera
  de México según delivery_by_region. Audiencias compradas u operación
  foránea.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from typing import Any

from .storage import AdStore
from .aggregates import canonical_mx_region

logger = logging.getLogger(__name__)

# Conservador a propósito: mejor perderse algunos medios que etiquetar de
# "medio" a páginas que no lo son. Se evalúa sobre el nombre normalizado.
_MEDIA_RE = re.compile(
    r"\b(noticias?|noticiero|news|diario|periodico|semanario|informativo|"
    r"reportero?s?|prensa|radio|television|tv|canal|medios|editorial|"
    r"al dia|en linea|digital news)\b"
)

_SIGNALS_QUERY = """
SELECT page_id, MAX(page_name) AS page_name, COUNT(*) AS ads,
       SUM(spend_lower) AS spend_lower,
       CASE WHEN SUM(spend_upper IS NULL) > 0 THEN NULL ELSE SUM(spend_upper) END AS spend_upper,
       MAX(spend_upper IS NULL) AS upper_unbounded,
       SUM(CASE WHEN bylines IS NULL OR TRIM(bylines) = '' THEN 1 ELSE 0 END) AS ads_sin_pagador,
       COUNT(DISTINCT CASE WHEN bylines IS NOT NULL AND TRIM(bylines) != '' THEN bylines END)
           AS pagadores_distintos
FROM ads
WHERE (:start IS NULL OR ad_delivery_start_time >= :start)
  AND (:end IS NULL OR ad_delivery_start_time <= :end)
GROUP BY page_id
"""

_BYLINE_ROWS_QUERY = """
SELECT page_id, page_name, bylines, spend_lower, delivery_by_region
FROM ads
WHERE (:start IS NULL OR ad_delivery_start_time >= :start)
  AND (:end IS NULL OR ad_delivery_start_time <= :end)
"""


def _norm(text: str | None) -> str:
    if not text:
        return ""
    stripped = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^\w\s]", " ", stripped.lower()).split())


def page_signals(store: AdStore, start: str | None = None, end: str | None = None):
    """Una fila por página con las señales v1 y su contexto de gasto."""
    import pandas as pd

    base = pd.read_sql_query(_SIGNALS_QUERY, store.conn, params={"start": start, "end": end})

    # Recorrido por anuncio para señales que requieren comparar/parsear campos.
    ajeno: dict[str, int] = {}
    total: dict[str, int] = {}
    spend_mx: dict[str, float] = {}
    spend_ext: dict[str, float] = {}
    cur = store.conn.execute(_BYLINE_ROWS_QUERY, {"start": start, "end": end})
    for page_id, page_name, bylines, spend_lower, region_json in cur:
        total[page_id] = total.get(page_id, 0) + 1
        if bylines and _norm(bylines) and _norm(bylines) != _norm(page_name):
            ajeno[page_id] = ajeno.get(page_id, 0) + 1
        if region_json:
            try:
                items = json.loads(region_json)
            except (TypeError, ValueError):
                items = []
            for item in items if isinstance(items, list) else []:
                try:
                    pct = float(item.get("percentage", 0))
                except (TypeError, ValueError):
                    continue
                monto = (spend_lower or 0.0) * pct
                if canonical_mx_region(item.get("region")) is None:
                    spend_ext[page_id] = spend_ext.get(page_id, 0.0) + monto
                else:
                    spend_mx[page_id] = spend_mx.get(page_id, 0.0) + monto

    base["pct_sin_pagador"] = base["ads_sin_pagador"] / base["ads"]
    base["pct_pagador_ajeno"] = base["page_id"].map(
        lambda p: ajeno.get(p, 0) / total.get(p, 1))
    base["perfil_de_medio"] = base["page_name"].map(
        lambda n: bool(_MEDIA_RE.search(_norm(n))))
    denom = base["page_id"].map(
        lambda p: spend_mx.get(p, 0.0) + spend_ext.get(p, 0.0))
    # denom 0 (sin región o gasto 0) → NaN: "sin dato", distinto de 0% extranjero
    base["pct_entrega_extranjera"] = (
        base["page_id"].map(lambda p: spend_ext.get(p, 0.0)) / denom.where(denom > 0)
    )

    # Conteo de señales activas con umbrales documentados (ver data/README.md).
    # Es un filtro de priorización editorial, no un veredicto.
    base["senales_activas"] = (
        (base["pct_sin_pagador"] >= 0.5).astype(int)
        + (base["pagadores_distintos"] >= 3).astype(int)
        + (base["pct_pagador_ajeno"] >= 0.5).astype(int)
        + base["perfil_de_medio"].astype(int)
        + (base["pct_entrega_extranjera"].fillna(0.0) >= 0.2).astype(int)
    )
    return base.sort_values(
        ["senales_activas", "spend_lower"], ascending=False
    ).reset_index(drop=True)
