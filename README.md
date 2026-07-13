# politica-meta

Descarga y análisis de anuncios políticos en Facebook e Instagram (México), usando la
[Meta Ad Library API](https://www.facebook.com/ads/library/api/). Proyecto de
[Umbral](https://umbralmx.github.io/).

**Objetivo:** hacer trazable el gasto político en Meta — incluido el que fluye por
medios locales y páginas de terceros — y eventualmente publicarlo en un dashboard
de Streamlit. La metodología de análisis está en [`METODOLOGIA.md`](METODOLOGIA.md).
El contexto completo del proyecto (para colaboradores o asistentes de IA) está en
[`CONTEXT.md`](CONTEXT.md).

## Requisitos de acceso (una sola vez)

La API de la Ad Library es pública pero requiere:

1. **Cuenta de desarrollador** en [developers.facebook.com](https://developers.facebook.com) y crear una app (cualquier tipo).
2. **Confirmación de identidad** en [facebook.com/ID](https://www.facebook.com/ID). Es obligatoria para consultar anuncios de política/temas sociales vía API. Tarda 1–2 días.
3. **Token de acceso**: genera un token de usuario en el [Graph API Explorer](https://developers.facebook.com/tools/explorer/) y extiéndelo a ~60 días en el [Access Token Debugger](https://developers.facebook.com/tools/debug/accesstoken/) ("Extend Access Token"). No requiere permisos especiales.

## Instalación

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # y pega tu token
```

## Uso

```bash
# Descargar TODOS los anuncios políticos entregados en México en un periodo
# (sin --search-terms la API devuelve el universo completo del país)
python -m politica_meta scrape --start 2026-01-01 --end 2026-06-30

# Búsqueda dirigida por palabras clave o por páginas específicas
python -m politica_meta scrape --start 2026-01-01 --end 2026-06-30 --search-terms "nombre del candidato"
python -m politica_meta scrape --start 2026-01-01 --end 2026-06-30 --page-ids 1234567890,9876543210

# Buscar propaganda NO declarada como política (ver metodología, §6)
python -m politica_meta scrape --start 2026-01-01 --end 2026-06-30 --ad-type ALL --search-terms "nombre del candidato"

# Resumen y exportación
python -m politica_meta stats
python -m politica_meta export --out data/ads_mx.parquet   # o .csv

# Tablas agregadas (todas, CSV + Parquet): página, región, página×región, mes
python -m politica_meta aggregate --start 2026-01-01 --end 2026-06-30

# Solo una familia de tablas
python -m politica_meta aggregate --by page_region

# Ranking de páginas por gasto en una entidad (el gráfico por estado)
python -m politica_meta aggregate --region "Sonora" --top 30
```

### Relación con el Ad Library Report

El [Ad Library Report](https://www.facebook.com/ads/library/report/) (gasto total,
por anunciante y por ubicación desde agosto de 2020) **no está disponible vía API**:
solo como descarga manual de CSVs desde esa página. Este proyecto reproduce sus dos
tablas centrales a partir de los datos por anuncio de la API (`aggregate`):

- `spend_by_page_region` — **tabla central**: una fila por (página, entidad), con el
  gasto de cada anuncio prorrateado por los porcentajes de `delivery_by_region`.
  Como esos porcentajes son participación de impresiones (no de gasto verificado),
  cada valor es un intervalo *modelado* (`estimate_type = region_allocated`).
- `spend_by_region_nonmx` — misma estructura para regiones extranjeras y "Unknown"
  (no se descartan: entrega al extranjero es una señal en sí misma), separadas de
  las tablas de análisis de México.
- `spend_by_page` — marginal directo por página (equivale a "gasto por anunciante").
- `spend_by_region` — marginal por entidad (32 estados, nombres canónicos: la API
  usa variantes como "Distrito Federal", "State of Mexico" o "Querétaro Arteaga").
- `spend_by_month` y `spend_by_page_month` — cohorte por mes de inicio de entrega
  (`time_method = start_month_cohort`): el gasto de un anuncio largo cae completo en
  su mes de inicio; el prorrateo por días activos queda documentado como v2.

Reglas duras: el gasto es siempre intervalo `[spend_lower, spend_upper]`; si un
anuncio cae en el bucket superior abierto de Meta, `spend_upper` queda NULL y
`upper_unbounded = True` se propaga a toda celda/marginal que lo toque (nunca se
tapa el techo con la cota inferior). Cada corrida ejecuta una reconciliación
automática (suma asignada ≈ suma directa por página) que aborta con warnings si la
asignación pierde dinero en el camino.

Diferencia clave: el reporte oficial publica totales exactos; la API solo da rangos
por anuncio, así que estos agregados son **intervalos** `[spend_lower, spend_upper]`
(consistente con la metodología, §5). A cambio, la vía API es automatizable, filtrable
por fechas arbitrarias y llega al detalle de anuncio, que el reporte no ofrece. Los
CSVs oficiales pueden descargarse a mano de vez en cuando como *ground truth* para
validar los agregados.

### Cómo funciona la descarga

- El periodo se parte en **ventanas de 7 días** (`--window-days`) porque la paginación
  profunda del endpoint falla con conjuntos grandes de resultados.
- Cada ventana completada queda registrada: si el proceso se interrumpe (rate limit,
  red, Ctrl-C), **volver a correr el mismo comando retoma donde se quedó**.
- `--refresh` re-descarga ventanas completadas; útil porque los rangos de gasto de un
  anuncio crecen mientras sigue activo.
- Los anuncios se guardan en SQLite (`data/ads_mx.sqlite`) con upsert por ID: sin
  duplicados y siempre con el snapshot más reciente.

## Diccionario de datos (campos clave)

| Campo | Descripción |
|---|---|
| `id` | ID del anuncio en la Ad Library (`https://www.facebook.com/ads/library/?id=<id>`) |
| `page_id`, `page_name` | Página que pautó el anuncio |
| `bylines` | Renglón "Pagado por" — la entidad que declaró financiar el anuncio |
| `spend_lower`, `spend_upper` | **Rango** de gasto (Meta no da cifras exactas), en `currency` (normalmente MXN) |
| `impressions_lower/upper` | Rango de impresiones |
| `demographic_distribution` | Distribución de audiencia por edad y género (JSON) |
| `delivery_by_region` | Distribución por entidad federativa (JSON) |
| `publisher_platforms` | Facebook, Instagram, Messenger, Threads… |
| `creative_bodies` | Textos del anuncio (JSON, puede haber varias versiones) |
| `ad_snapshot_url` | Vista del anuncio archivado (requiere token) |

## Limitaciones conocidas

- **Gasto e impresiones vienen en rangos**, no cifras exactas. Todo agregado debe
  reportarse como intervalo (ver metodología, §5).
- Solo cubre anuncios **pagados** y **clasificados por Meta como políticos**; la
  propaganda pagada que el anunciante no declaró como política requiere búsqueda
  activa con `--ad-type ALL` (sin datos de gasto).
- México no recibe los campos de segmentación detallada que Meta publica para la
  UE (`target_ages`, `beneficiary_payers`, etc.).
- La API limita peticiones por hora; el cliente reintenta con backoff automático,
  pero una descarga de un año completo puede tomar horas.

## Estructura

```
politica_meta/
  client.py    # cliente HTTP: paginación, rate limits, reintentos
  scraper.py   # barridos por ventanas de fechas, reanudables
  storage.py   # SQLite con upsert y bitácora de ventanas
  export.py    # exportación a CSV/Parquet
  aggregates.py# gasto por anunciante y por región (equivalente al Ad Library Report)
  __main__.py  # CLI
METODOLOGIA.md # metodología de atribución de beneficiario, postura y gasto
```

## Dashboard (Streamlit)

```bash
streamlit run app.py
```

Modo instrumento (oscuro) con la identidad de umbral_. Tres vistas: **Panorama**
(intervalo de gasto mensual y ranking de páginas), **Por entidad** (ranking de
páginas por estado, con la nota de que el prorrateo regional es modelado) y
**Por anunciante** (detalle de página: serie mensual, huella territorial y liga a
sus anuncios en la Ad Library pública). Lee `data/aggregates/*.parquet`; corre
`aggregate` antes para refrescar.

## Hoja de ruta

1. ✅ Scraper de la Ad Library
2. ✅ Agregados página×región×mes con intervalos y reconciliación
3. ✅ Dashboard en Streamlit v1 (identidad umbral_)
4. ⬜ Diccionario de actores (candidatos, partidos, alias) y pipeline de atribución
5. ⬜ Clasificación de postura (favor/contra) — ver `METODOLOGIA.md`
