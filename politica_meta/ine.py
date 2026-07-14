"""Cruce con fiscalización del INE (metodología §7).

Compara, por actor, el gasto observado en la Ad Library contra el gasto en
redes sociales reportado ante el INE (Sistema Integral de Fiscalización).
La inconsistencia documentable es: **cota inferior observada > reportado** —
el actor gastó en Meta, como mínimo, más de lo que declaró.

Entrada: un CSV que se llena a mano desde los reportes públicos del SIF
(no hay API); ruta por defecto `dictionaries/ine_fiscalizacion.csv`:

    actor_id,periodo_inicio,periodo_fin,gasto_redes_reportado,fuente,notas
    maynez,2026-01-15,2026-05-28,1500000,"SIF informe X",""

- `actor_id` debe existir en dictionaries/actores.csv.
- `periodo_*` acota por fecha de inicio de entrega del anuncio (YYYY-MM-DD),
  alineado al calendario de precampaña/campaña que cubre la fiscalización.
- `gasto_redes_reportado` en MXN.

Se comparan dos universos, del más al menos atribuible:
- `bylines`: anuncios cuyo "Pagado por" menciona al actor — gasto declarado
  en Meta directamente atribuible.
- `menciones`: todos los anuncios que mencionan al actor (contexto; incluye
  pauta de terceros y cobertura, NO es gasto del actor).
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Any

from .storage import AdStore

logger = logging.getLogger(__name__)

DEFAULT_INE_CSV = "dictionaries/ine_fiscalizacion.csv"

_SPEND_QUERY = """
SELECT
    SUM(a.spend_lower) AS spend_lower,
    CASE WHEN SUM(a.spend_upper IS NULL) > 0 THEN NULL ELSE SUM(a.spend_upper) END AS spend_upper,
    COUNT(*) AS ads
FROM ad_actors aa JOIN ads a ON a.id = aa.ad_id
WHERE aa.actor_id = :actor
  AND (:solo_bylines = 0 OR aa.locations LIKE '%bylines%')
  AND (:start IS NULL OR a.ad_delivery_start_time >= :start)
  AND (:end IS NULL OR a.ad_delivery_start_time <= :end)
"""


def cross_check(store: AdStore, ine_csv: str | Path = DEFAULT_INE_CSV) -> list[dict[str, Any]]:
    path = Path(ine_csv)
    if not path.exists():
        raise SystemExit(
            f"No existe {path}. Llénalo a mano desde los reportes del SIF "
            "(formato documentado en politica_meta/ine.py)."
        )
    rows: list[dict[str, Any]] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            actor = r["actor_id"].strip()
            start = r.get("periodo_inicio") or None
            end = r.get("periodo_fin") or None
            reportado = float(r["gasto_redes_reportado"])
            out: dict[str, Any] = {
                "actor_id": actor,
                "periodo": f"{start or '…'} → {end or '…'}",
                "gasto_redes_reportado": reportado,
                "fuente": r.get("fuente", ""),
            }
            for universo, solo_bylines in (("bylines", 1), ("menciones", 0)):
                lo, hi, ads = store.conn.execute(
                    _SPEND_QUERY,
                    {"actor": actor, "solo_bylines": solo_bylines,
                     "start": start, "end": end},
                ).fetchone()
                out[f"{universo}_lower"] = lo or 0.0
                out[f"{universo}_upper"] = hi
                out[f"{universo}_ads"] = ads
            # La inconsistencia dura usa SOLO el universo atribuible (bylines)
            # y SOLO la cota inferior: nunca acusar con la cota superior.
            out["inconsistencia"] = out["bylines_lower"] > reportado
            rows.append(out)
    return rows
