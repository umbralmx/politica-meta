"""Postura (stance) por par anuncio×actor — metodología §4.

Etiqueta CADA par (anuncio, actor) de `ad_actors`: ¿el anuncio es favorable,
desfavorable o neutral HACIA ese actor? No es sentimiento del texto — un
anuncio puede ser favorable a un actor y desfavorable a otro a la vez.

Disciplina metodológica:
- El LLM etiqueta; un humano valida. `sample-gold` genera la muestra para
  etiquetado humano ciego; `validate` publica accuracy, F1 por clase y kappa
  de Cohen contra ese gold set. Sin métricas publicadas, las etiquetas no se
  usan en producto (§4.3).
- Cada etiqueta guarda modelo, batch y justificación: auditable por anuncio.

Infraestructura: Anthropic Message Batches (asíncrono, 50% del precio) con
structured outputs (JSON garantizado contra esquema). Requiere el paquete
`anthropic` y ANTHROPIC_API_KEY en el entorno o .env — es dependencia del
pipeline, NO del dashboard (por eso no está en requirements.txt).

Uso:
    python -m politica_meta stance sample-gold          # CSV para etiquetar a mano
    python -m politica_meta stance submit [--limit N]   # crea el batch
    python -m politica_meta stance fetch --batch-id ID  # descarga resultados
    python -m politica_meta stance validate --gold data/stance/gold.csv
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import logging
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

from .storage import AdStore

logger = logging.getLogger(__name__)

# Opus 4.8 por defecto: la calidad de la etiqueta es el producto y el batch ya
# descuenta 50%. Con --model claude-haiku-4-5 el costo baja ~5x a cambio de
# calidad; si se cambia de modelo hay que re-validar contra el gold set.
DEFAULT_MODEL = "claude-opus-4-8"
STANCE_DIR = Path("data/stance")

STANCE_SCHEMA = {
    "type": "object",
    "properties": {
        "postura": {
            "type": "string",
            "enum": ["favorable", "desfavorable", "neutral", "sin_postura"],
            "description": "Postura del anuncio HACIA el actor indicado",
        },
        "confianza": {"type": "string", "enum": ["alta", "media", "baja"]},
        "justificacion": {
            "type": "string",
            "description": "Una o dos frases citando el texto que sustenta la etiqueta",
        },
    },
    "required": ["postura", "confianza", "justificacion"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """\
Eres un codificador de contenido político para un proyecto periodístico de \
transparencia en México. Recibirás el texto de un anuncio pagado en Meta \
(Facebook/Instagram) y un actor político mencionado en él. Tu tarea es \
etiquetar la POSTURA DEL ANUNCIO HACIA ESE ACTOR, no el tono general del texto.

Etiquetas:
- favorable: el anuncio promueve, defiende, celebra o pide apoyo para el actor.
- desfavorable: el anuncio critica, ataca, responsabiliza o ridiculiza al actor.
- neutral: el actor aparece en cobertura informativa sin valoración discernible.
- sin_postura: la mención es incidental o el texto es insuficiente para juzgar.

Reglas:
- Juzga solo con el texto dado; no uses conocimiento externo sobre el actor.
- Un anuncio puede ser favorable a un actor y desfavorable a otro; etiqueta \
solo al actor indicado.
- La ironía y el sarcasmo cuentan por su intención, no por su literalidad.
- Si el texto es propaganda del propio actor (habla en primera persona o pide \
el voto por él), es favorable.
- Ante duda genuina entre dos etiquetas, elige la menos valorativa y baja la \
confianza."""


def _client():
    try:
        import anthropic
    except ImportError:
        raise SystemExit(
            "Falta el paquete `anthropic` (dependencia del pipeline de stance, "
            "no del dashboard): pip install anthropic"
        )
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    return anthropic.Anthropic()


STANCE_TABLE = """
CREATE TABLE IF NOT EXISTS ad_stance (
    ad_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    postura TEXT NOT NULL,
    confianza TEXT NOT NULL,
    justificacion TEXT NOT NULL,
    model TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    labeled_at TEXT NOT NULL,
    PRIMARY KEY (ad_id, actor_id)
);
"""

_PAIRS_QUERY = """
SELECT aa.ad_id, aa.actor_id, a.page_name, a.bylines,
       a.creative_bodies, a.link_titles, a.link_descriptions
FROM ad_actors aa JOIN ads a ON a.id = aa.ad_id
ORDER BY aa.ad_id, aa.actor_id
"""


def _ad_text(row: dict[str, Any], max_chars: int = 1500) -> str:
    parts: list[str] = []
    for field in ("creative_bodies", "link_titles", "link_descriptions"):
        raw = row.get(field)
        if not raw:
            continue
        try:
            items = json.loads(raw)
        except (TypeError, ValueError):
            items = [str(raw)]
        parts.extend(str(p) for p in items if p)
    text = " ".join(dict.fromkeys(parts))  # dedup: los creativos suelen repetirse
    return " ".join(text.split())[:max_chars]


def pairs_universe(store: AdStore, only_unlabeled: bool = False) -> list[dict[str, Any]]:
    store.conn.executescript(STANCE_TABLE)
    labeled: set[tuple[str, str]] = set()
    if only_unlabeled:
        labeled = set(store.conn.execute("SELECT ad_id, actor_id FROM ad_stance"))
    cur = store.conn.execute(_PAIRS_QUERY)
    cols = [d[0] for d in cur.description]
    out = []
    for values in cur:
        row = dict(zip(cols, values))
        if (row["ad_id"], row["actor_id"]) in labeled:
            continue
        text = _ad_text(row)
        if not text:
            continue  # sin texto no hay nada que etiquetar
        row["texto"] = text
        out.append(row)
    return out


def _user_prompt(row: dict[str, Any]) -> str:
    return (
        f"Actor a evaluar: {row['actor_id']}\n"
        f"Página que pauta: {row.get('page_name') or '(desconocida)'}\n"
        f"Pagado por: {row.get('bylines') or '(sin disclaimer)'}\n\n"
        f"Texto del anuncio:\n{row['texto']}\n\n"
        f"Etiqueta la postura del anuncio hacia el actor indicado."
    )


def _request(row: dict[str, Any], model: str) -> dict[str, Any]:
    return {
        "custom_id": f"{row['ad_id']}__{row['actor_id']}"[:64],
        "params": {
            "model": model,
            "max_tokens": 1024,
            # cache_control: el system compartido solo se cachea si supera el
            # mínimo del modelo (4096 tokens en Opus 4.8); inofensivo si no.
            "system": [{"type": "text", "text": SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"}}],
            "output_config": {"format": {"type": "json_schema", "schema": STANCE_SCHEMA}},
            "messages": [{"role": "user", "content": _user_prompt(row)}],
        },
    }


def sample_gold(store: AdStore, out_csv: str | Path, n: int = 300, seed: int = 42) -> int:
    """Muestra estratificada por actor para etiquetado humano CIEGO (sin la
    etiqueta del LLM). Round-robin entre actores para que los chicos no queden
    fuera; el humano llena `postura_humana` con el mismo esquema de etiquetas."""
    by_actor: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in pairs_universe(store):
        by_actor[row["actor_id"]].append(row)
    rng = random.Random(seed)
    for rows in by_actor.values():
        rng.shuffle(rows)
    picked: list[dict[str, Any]] = []
    actors = sorted(by_actor)
    i = 0
    while len(picked) < n and any(by_actor.values()):
        actor = actors[i % len(actors)]
        if by_actor[actor]:
            picked.append(by_actor[actor].pop())
        i += 1
    out = Path(out_csv)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["ad_id", "actor_id", "texto", "url", "postura_humana", "notas"])
        for r in picked:
            w.writerow([r["ad_id"], r["actor_id"], r["texto"],
                        f"https://www.facebook.com/ads/library/?id={r['ad_id']}", "", ""])
    return len(picked)


def submit_batch(store: AdStore, model: str = DEFAULT_MODEL,
                 limit: int | None = None) -> str:
    """Crea el batch de etiquetado (solo pares aún sin etiqueta). Guarda un
    manifiesto en data/stance/batches/ para poder recuperarlo con `fetch`."""
    rows = pairs_universe(store, only_unlabeled=True)
    if limit:
        rows = rows[:limit]
    if not rows:
        raise SystemExit("No hay pares (anuncio, actor) pendientes de etiquetar.")
    client = _client()
    batch = client.messages.batches.create(requests=[_request(r, model) for r in rows])
    manifest = {
        "batch_id": batch.id,
        "model": model,
        "pairs": len(rows),
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }
    out = STANCE_DIR / "batches"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{batch.id}.json").write_text(json.dumps(manifest, indent=2))
    logger.info("Batch %s creado con %d pares (modelo %s)", batch.id, len(rows), model)
    return batch.id


def fetch_batch(store: AdStore, batch_id: str) -> dict[str, int]:
    """Descarga resultados de un batch terminado y los upserta en ad_stance."""
    client = _client()
    batch = client.messages.batches.retrieve(batch_id)
    if batch.processing_status != "ended":
        raise SystemExit(
            f"El batch sigue en estado '{batch.processing_status}' "
            f"({batch.request_counts.processing} en proceso). Reintenta más tarde."
        )
    store.conn.executescript(STANCE_TABLE)
    manifest_path = STANCE_DIR / "batches" / f"{batch_id}.json"
    model = "?"
    if manifest_path.exists():
        model = json.loads(manifest_path.read_text()).get("model", "?")
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    counts = {"ok": 0, "errored": 0, "refusal": 0, "malformed": 0}
    rows: list[tuple] = []
    for result in client.messages.batches.results(batch_id):
        if result.result.type != "succeeded":
            counts["errored"] += 1
            continue
        msg = result.result.message
        if msg.stop_reason == "refusal":
            counts["refusal"] += 1
            continue
        text = next((b.text for b in msg.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except ValueError:
            counts["malformed"] += 1
            continue
        ad_id, _, actor_id = result.custom_id.partition("__")
        rows.append((ad_id, actor_id, data["postura"], data["confianza"],
                     data["justificacion"], msg.model, batch_id, now))
        counts["ok"] += 1
    with store.conn:
        store.conn.executemany(
            "INSERT OR REPLACE INTO ad_stance VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows
        )
    logger.info("Batch %s (modelo %s): %s", batch_id, model, counts)
    return counts


def export_stance(store: AdStore, out_dir: str | Path = "data/aggregates") -> int:
    import pandas as pd

    df = pd.read_sql_query(
        """
        SELECT s.*, a.page_id, a.page_name, a.spend_lower, a.spend_upper,
               a.ad_delivery_start_time
        FROM ad_stance s JOIN ads a ON a.id = s.ad_id
        """,
        store.conn,
    )
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "ad_stance.csv", index=False)
    df.to_parquet(out / "ad_stance.parquet", index=False)
    return len(df)


def validate(store: AdStore, gold_csv: str | Path) -> dict[str, Any]:
    """Métricas del LLM contra el gold set humano: accuracy, precisión/recall/F1
    por clase, macro-F1 y kappa de Cohen. Estas métricas SE PUBLICAN junto con
    cualquier producto que use las etiquetas (metodología §4.3)."""
    gold: dict[tuple[str, str], str] = {}
    with open(gold_csv, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            label = (row.get("postura_humana") or "").strip().lower()
            if label:
                gold[(row["ad_id"], row["actor_id"])] = label
    if not gold:
        raise SystemExit(f"{gold_csv} no tiene etiquetas en `postura_humana`.")

    llm = dict(
        ((ad, actor), p) for ad, actor, p in store.conn.execute(
            "SELECT ad_id, actor_id, postura FROM ad_stance"
        )
    )
    pairs = [(g, llm[k]) for k, g in gold.items() if k in llm]
    if not pairs:
        raise SystemExit("Ningún par del gold set tiene etiqueta del LLM todavía.")

    labels = sorted({g for g, _ in pairs} | {p for _, p in pairs})
    confusion = {g: {p: 0 for p in labels} for g in labels}
    for g, p in pairs:
        confusion[g][p] += 1
    total = len(pairs)
    accuracy = sum(confusion[l][l] for l in labels) / total

    per_class = {}
    f1s = []
    for l in labels:
        tp = confusion[l][l]
        fp = sum(confusion[g][l] for g in labels if g != l)
        fn = sum(confusion[l][p] for p in labels if p != l)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        per_class[l] = {"precision": prec, "recall": rec, "f1": f1,
                        "soporte": tp + fn}
        f1s.append(f1)

    # Kappa de Cohen: acuerdo corregido por azar
    p_o = accuracy
    p_e = sum(
        (sum(confusion[l].values()) / total)
        * (sum(confusion[g][l] for g in labels) / total)
        for l in labels
    )
    kappa = (p_o - p_e) / (1 - p_e) if p_e < 1 else 1.0

    metrics = {
        "n_validados": total,
        "n_gold": len(gold),
        "accuracy": accuracy,
        "macro_f1": sum(f1s) / len(f1s),
        "kappa_cohen": kappa,
        "por_clase": per_class,
        "confusion": confusion,
        "generado": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }
    STANCE_DIR.mkdir(parents=True, exist_ok=True)
    (STANCE_DIR / "validacion.json").write_text(
        json.dumps(metrics, indent=2, ensure_ascii=False)
    )
    return metrics
