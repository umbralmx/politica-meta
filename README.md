# politica-meta

Descarga y análisis de anuncios políticos en Facebook e Instagram (México), usando la
[Meta Ad Library API](https://www.facebook.com/ads/library/api/). Proyecto de
[Umbral](https://umbralmx.github.io/).

**Objetivo:** hacer trazable el gasto político en Meta — incluido el que fluye por
medios locales y páginas de terceros — y eventualmente publicarlo en un dashboard
de Streamlit. La metodología de análisis está en [`METODOLOGIA.md`](METODOLOGIA.md).

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
```

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
  __main__.py  # CLI
METODOLOGIA.md # metodología de atribución de beneficiario, postura y gasto
```

## Hoja de ruta

1. ✅ Scraper de la Ad Library
2. ⬜ Diccionario de actores (candidatos, partidos, alias) y pipeline de atribución
3. ⬜ Clasificación de postura (favor/contra) — ver `METODOLOGIA.md`
4. ⬜ Dashboard en Streamlit (estilo [desaparecidosmx](https://desaparecidosmx.streamlit.app/))
