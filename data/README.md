# Diccionario de datos — `data/`

Archivos generados por el pipeline de `politica_meta`. Nada en esta carpeta se
versiona en git (salvo este README); todo se regenera con los comandos indicados.

## Convenciones globales

- **El gasto siempre es un intervalo** `[spend_lower, spend_upper]` en MXN (ver
  `currency` por anuncio). Meta solo publica rangos por anuncio; ninguna cifra aquí
  es exacta y así debe reportarse: *"entre $X y $Y"*.
- **`upper_unbounded`**: si un anuncio cae en el bucket superior abierto de Meta
  (sin cota superior), `spend_upper` queda **vacío (NULL)** y este flag es `True`.
  El flag se propaga a cualquier agregado que toque ese anuncio. Nunca se "tapa"
  el techo con la cota inferior.
- Cada tabla agregada existe en CSV y Parquet con el mismo nombre y esquema.
- Los nombres de entidad federativa están **canonicalizados** (la API usa variantes
  como "Distrito Federal", "State of Mexico", "Querétaro Arteaga", "Michoacán de
  Ocampo"; aquí aparecen como Ciudad de México, Estado de México, Querétaro,
  Michoacán, etc.).

## `ads_mx.sqlite` — base cruda

Generada por `python -m politica_meta scrape`. Tablas:

- **`ads`** — un renglón por anuncio (upsert por `id`: sin duplicados, siempre el
  snapshot más reciente). Columnas planas (página, fechas, cotas de gasto e
  impresiones) + columnas JSON (`creative_bodies`, `demographic_distribution`,
  `delivery_by_region`, …) + `raw` (la respuesta original completa de la API, que
  se conserva como evidencia).
- **`windows`** — bitácora de ventanas de fechas ya descargadas (permite reanudar
  un barrido interrumpido). Uso interno.

## `ads_mx.csv` / `ads_mx.parquet` — export plano

`python -m politica_meta export --out data/ads_mx.csv`

| Columna | Descripción |
|---|---|
| `id` | ID del anuncio; vista pública: `facebook.com/ads/library/?id=<id>` |
| `page_id`, `page_name` | Página que pautó |
| `bylines` | Renglón "Pagado por" (pagador declarado); vacío = sin disclaimer |
| `currency` | Moneda (normalmente MXN) |
| `ad_creation_time`, `ad_delivery_start_time`, `ad_delivery_stop_time` | Fechas UTC; stop vacío = seguía activo |
| `spend_lower`, `spend_upper` | Intervalo de gasto del anuncio |
| `spend_mid` | Punto medio — **solo para ordenar/graficar tamaños, nunca para citar** |
| `impressions_lower/upper`, `audience_lower/upper` | Intervalos de impresiones y audiencia estimada |
| `creative_bodies`, `link_titles`, `link_captions`, `link_descriptions` | Textos del anuncio (JSON: puede haber varias versiones) |
| `languages`, `publisher_platforms` | JSON (facebook, instagram, threads, …) |
| `demographic_distribution` | JSON: % de audiencia por edad×género |
| `delivery_by_region` | JSON: % de entrega por región (incluye regiones extranjeras) |
| `ad_snapshot_url` | Vista del creativo archivado (requiere token en la URL) |
| `first_seen`, `last_seen` | Cuándo capturó/actualizó este scraper el anuncio |

## `aggregates/` — tablas de análisis

`python -m politica_meta aggregate [--start YYYY-MM-DD --end YYYY-MM-DD]`
(los filtros de fecha aplican sobre `ad_delivery_start_time`).

### `spend_by_page_region` — **tabla central** (página × entidad)

El gasto de cada anuncio se prorratea entre sus regiones según los porcentajes de
`delivery_by_region`. Esos porcentajes son participación de **impresiones**, no de
gasto verificado: cada valor es un intervalo **modelado**, marcado
`estimate_type = region_allocated`.

| Columna | Descripción |
|---|---|
| `page_id`, `page_name`, `bylines` | Página anunciante y pagador declarado |
| `region` | Entidad federativa (canónica, solo las 32) |
| `spend_lower`, `spend_upper`, `upper_unbounded` | Intervalo de gasto asignado a esa celda |
| `ad_touches` | Nº de anuncios que tocan la celda. **No es** un conteo de anuncios exclusivos: un anuncio con entrega en 5 estados cuenta en 5 celdas |
| `estimate_type` | Siempre `region_allocated` (valor modelado) |

### `spend_by_region_nonmx` — regiones fuera de México

Mismo esquema que la anterior, para regiones extranjeras y `Unknown` (Texas,
California, etc.). No se descarta porque la entrega al extranjero o desconocida es
una señal en sí misma, pero se mantiene fuera de las tablas de análisis de México.

### `spend_by_page` — marginal directo por anunciante

Suma directa por página sobre todos sus anuncios (sin prorrateo): `ads` (conteo
real de anuncios), intervalos de gasto e impresiones, `first_ad`/`last_ad`.
Equivale al "gasto por anunciante" del Ad Library Report, en intervalos.

### `spend_by_region` — marginal por entidad

Agrupación del `spend_by_page_region` por `region` (las 32 entidades). Columnas:
`region`, `spend_lower`, `spend_upper`, `upper_unbounded`, `ad_touches`.

### `spend_by_month` y `spend_by_page_month` — series temporales

Cohorte por **mes de inicio de entrega** (`time_method = start_month_cohort`): el
gasto completo de un anuncio cae en el mes `YYYY-MM` en que empezó a entregarse.
Simplificación conocida y documentada: un anuncio que corrió 3 meses aparece
entero en su mes de inicio (el prorrateo por días activos es una v2 pendiente).
Columnas: `month`, [`page_id`, `page_name`,] `ads`, intervalos, `upper_unbounded`,
`time_method`.

### `top_pages_<entidad>.csv` — ranking por estado

`python -m politica_meta aggregate --region "Sonora" --top 30`
Filtra `spend_by_page_region` a una entidad y ordena por `spend_lower`
descendente. Es la gráfica por estado que antes solo se podía armar a mano con el
CSV exportable de Meta.

### Verificación automática

Cada corrida de `aggregate` reconcilia la suma asignada contra la suma directa por
página (mismo universo: anuncios con `delivery_by_region`). Discrepancias > 0.5%
se registran como warnings; si aparecen, la asignación tiene un bug y los CSVs no
deben publicarse.

## `scrape_2025_ytd.log`

Bitácora del barrido 2025-01-01 → 2026-07-13 (progreso por ventana, reintentos,
divisiones de ventana por fallos de paginación profunda de la API).
