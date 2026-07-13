"""Atribución de actores (metodología §3): ¿de qué actor habla cada anuncio?

Empata el diccionario versionado en dictionaries/actores.csv contra el texto
normalizado de cada anuncio. Reglas:

- Normalización: minúsculas, sin acentos, espacios colapsados; empate por
  límites de palabra (nunca subcadenas).
- `alias` son inequívocos y empatan solos. `alias_ambiguos` (nombres cortos,
  siglas que colisionan con palabras comunes: "pan", "mc", "claudia") solo
  cuentan si en el MISMO anuncio aparece un token de contexto del actor
  (su partido, cargo, entidad o un alias inequívoco).
- Se registra DÓNDE empató (body / page / bylines): una mención en bylines
  implica financiamiento declarado; en el cuerpo, solo tema (metodología §3.2).
- Un anuncio puede mencionar varios actores: la salida es anuncio × actor.
"""

from __future__ import annotations

import csv
import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .storage import AdStore

logger = logging.getLogger(__name__)

BODY_FIELDS = ["creative_bodies", "link_titles", "link_captions", "link_descriptions"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS ad_actors (
    ad_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    locations TEXT NOT NULL,      -- body|page|bylines (separadas por |)
    matched_aliases TEXT NOT NULL,
    via_ambiguous INTEGER NOT NULL, -- 1 = solo empató por alias ambiguo (con contexto)
    PRIMARY KEY (ad_id, actor_id)
);
CREATE INDEX IF NOT EXISTS idx_ad_actors_actor ON ad_actors (actor_id);
"""


def normalize(text: str) -> str:
    stripped = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return " ".join(stripped.lower().split())


def _word_regex(terms: list[str]) -> re.Pattern | None:
    terms = [t for t in terms if t]
    if not terms:
        return None
    alternation = "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))
    return re.compile(rf"\b(?:{alternation})\b")


@dataclass
class Actor:
    actor_id: str
    nombre: str
    tipo: str
    partido: str
    cargo: str
    entidad: str
    safe: re.Pattern | None
    ambiguous: re.Pattern | None
    context: re.Pattern | None = field(default=None)

    def match(self, texts: dict[str, str], full_text: str) -> dict[str, Any] | None:
        """texts: {location: normalized text}. Returns match record or None."""
        locations: list[str] = []
        aliases: set[str] = set()
        safe_hit = False
        for loc, text in texts.items():
            if not text:
                continue
            if self.safe:
                hits = self.safe.findall(text)
                if hits:
                    safe_hit = True
                    locations.append(loc)
                    aliases.update(hits)
                    continue
            if self.ambiguous:
                hits = self.ambiguous.findall(text)
                if hits:
                    locations.append(loc)
                    aliases.update(hits)
        if not locations:
            return None
        if not safe_hit:
            # Solo alias ambiguos: exigir evidencia adicional en el anuncio —
            # un token de contexto (que por construcción excluye a los propios
            # alias ambiguos) o al menos dos alias ambiguos distintos.
            has_context = bool(self.context and self.context.search(full_text))
            if not has_context and len(aliases) < 2:
                return None
        return {
            "actor_id": self.actor_id,
            "locations": "|".join(dict.fromkeys(locations)),
            "matched_aliases": "|".join(sorted(aliases)),
            "via_ambiguous": 0 if safe_hit else 1,
        }


def load_dictionary(path: str | Path) -> list[Actor]:
    actors: list[Actor] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            safe_terms = [normalize(a) for a in (row.get("alias") or "").split("|") if a.strip()]
            amb_terms = [normalize(a) for a in (row.get("alias_ambiguos") or "").split("|") if a.strip()]
            # Contexto: alias inequívocos + partido/entidad (frase completa) +
            # palabras largas del cargo. Se excluye todo lo que coincida con un
            # alias ambiguo para que un alias no se confirme a sí mismo (p. ej.
            # el partido "PAN" no valida al alias ambiguo "pan").
            context_terms = list(safe_terms)
            for k in ("partido", "entidad"):
                v = normalize(row.get(k) or "")
                if v and v != "nacional":
                    context_terms.append(v)
            context_terms.extend(
                w for w in normalize(row.get("cargo") or "").split() if len(w) >= 5
            )
            context_terms = [t for t in context_terms if t not in amb_terms]
            actors.append(Actor(
                actor_id=row["actor_id"].strip(),
                nombre=row["nombre"].strip(),
                tipo=row.get("tipo", "").strip(),
                partido=row.get("partido", "").strip(),
                cargo=row.get("cargo", "").strip(),
                entidad=row.get("entidad", "").strip(),
                safe=_word_regex(safe_terms),
                ambiguous=_word_regex(amb_terms),
                context=_word_regex(context_terms),
            ))
    if not actors:
        raise ValueError(f"Diccionario vacío o ilegible: {path}")
    logger.info("Diccionario: %d actores cargados de %s", len(actors), path)
    return actors


def _ad_texts(row: dict[str, Any]) -> dict[str, str]:
    body_parts: list[str] = []
    for f in BODY_FIELDS:
        raw = row.get(f)
        if not raw:
            continue
        try:
            parts = json.loads(raw)
        except (TypeError, ValueError):
            parts = [str(raw)]
        body_parts.extend(str(p) for p in parts if p)
    return {
        "body": normalize(" ".join(body_parts)),
        "page": normalize(row.get("page_name") or ""),
        "bylines": normalize(row.get("bylines") or ""),
    }


def match_all(
    store: AdStore, actors: list[Actor], start: str | None = None, end: str | None = None
) -> dict[str, Any]:
    """Empata todos los anuncios contra el diccionario y llena ad_actors.
    Devuelve estadísticas de cobertura."""
    store.conn.executescript(SCHEMA)
    with store.conn:
        store.conn.execute("DELETE FROM ad_actors")

    cur = store.conn.execute(
        f"""
        SELECT id, page_name, bylines, {", ".join(BODY_FIELDS)}
        FROM ads
        WHERE (:start IS NULL OR ad_delivery_start_time >= :start)
          AND (:end IS NULL OR ad_delivery_start_time <= :end)
        """,
        {"start": start, "end": end},
    )
    cols = [d[0] for d in cur.description]
    stats = {"ads_total": 0, "ads_matched": 0, "pairs": 0}
    # Los pares empatados se acumulan en memoria (son una fracción pequeña) y
    # se escriben hasta agotar el cursor de lectura: escribir con el cursor
    # abierto provoca "database is locked" si el barrido corre en paralelo.
    matches: list[tuple] = []
    for values in cur:
        row = dict(zip(cols, values))
        stats["ads_total"] += 1
        texts = _ad_texts(row)
        full_text = " ".join(texts.values())
        matched = False
        for actor in actors:
            m = actor.match(texts, full_text)
            if m:
                matched = True
                stats["pairs"] += 1
                matches.append((row["id"], m["actor_id"], m["locations"],
                                m["matched_aliases"], m["via_ambiguous"]))
        if matched:
            stats["ads_matched"] += 1
    for i in range(0, len(matches), 5000):
        _flush(store, matches[i:i + 5000])
    stats["coverage"] = stats["ads_matched"] / stats["ads_total"] if stats["ads_total"] else 0.0
    return stats


def _flush(store: AdStore, batch: list[tuple]) -> None:
    if not batch:
        return
    with store.conn:
        store.conn.executemany(
            "INSERT OR REPLACE INTO ad_actors VALUES (?, ?, ?, ?, ?)", batch
        )
    batch.clear()


def actor_summary(store: AdStore):
    """Resumen por actor: anuncios que lo mencionan y el intervalo de gasto de
    esos anuncios. OJO: es 'gasto de anuncios que mencionan al actor', no
    'gasto a favor del actor' — la postura (§4) es la fase siguiente."""
    import pandas as pd

    return pd.read_sql_query(
        """
        SELECT
            aa.actor_id,
            COUNT(*) AS ads,
            COUNT(DISTINCT a.page_id) AS pages,
            SUM(a.spend_lower) AS spend_lower,
            CASE WHEN SUM(a.spend_upper IS NULL) > 0 THEN NULL ELSE SUM(a.spend_upper) END AS spend_upper,
            MAX(a.spend_upper IS NULL) AS upper_unbounded,
            SUM(CASE WHEN aa.locations LIKE '%bylines%' THEN 1 ELSE 0 END) AS ads_en_bylines
        FROM ad_actors aa JOIN ads a ON a.id = aa.ad_id
        GROUP BY aa.actor_id
        ORDER BY spend_lower DESC
        """,
        store.conn,
    )


def export_matches(store: AdStore, out_dir: str | Path) -> int:
    import pandas as pd

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    df = pd.read_sql_query(
        """
        SELECT aa.*, a.page_id, a.page_name, a.bylines, a.spend_lower, a.spend_upper,
               a.ad_delivery_start_time
        FROM ad_actors aa JOIN ads a ON a.id = aa.ad_id
        """,
        store.conn,
    )
    df.to_csv(out / "ad_actors.csv", index=False)
    df.to_parquet(out / "ad_actors.parquet", index=False)
    return len(df)
