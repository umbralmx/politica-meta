# Metodología: propaganda política encubierta en Meta (México)

**Pregunta central:** ¿qué mensajes pagados en Facebook/Instagram favorecen o atacan a
qué actor político, cuánto dinero implican, y qué parte de ese gasto fluye por páginas
que no son del candidato ni del partido (medios locales, páginas de "noticias",
influencers, páginas fantasma)?

La unidad de análisis es el **anuncio** (fila de la base descargada por el scraper);
las unidades de agregación son la **página** anunciante, el **pagador** (`bylines`) y
el **actor político beneficiado**.

---

## 1. Universo y sus límites

La Ad Library cubre únicamente anuncios **pagados** que Meta clasificó (o el anunciante
declaró) como de **política o temas sociales**. Quedan fuera, y hay que decirlo en toda
publicación:

- Contenido orgánico pagado "por fuera" (transferencias directas a páginas/influencers
  sin pauta publicitaria). No es observable aquí; sí lo es su *amplificación* pagada.
- Anuncios pagados que el anunciante **no declaró como políticos** y que Meta no
  detectó. Estos sí son parcialmente recuperables (§6).
- El gasto viene en **rangos** (p. ej. $100–$499 MXN); nunca hay cifra exacta.

## 2. Pipeline general

```
scrape (todo MX) ──► atribución de actor (§3) ──► clasificación de postura (§4)
                                                        │
   detección de anuncios no declarados (§6) ◄──── cuantificación de gasto (§5)
                                                        │
                        tipología de páginas (§6) ──► cruce con INE (§7) ──► dashboard
```

## 3. Atribución: ¿de qué actor habla el anuncio?

Antes de medir postura hay que saber a quién menciona cada anuncio.

1. **Diccionario de actores** (mantenido a mano, versionado en el repo):
   una fila por actor con `actor_id`, nombre completo, alias y apodos, variantes de
   escritura (con/sin acentos), cuentas oficiales, partido, cargo/candidatura, entidad.
   Fuentes: registros de candidaturas del INE/OPLEs, verificación manual.
2. **Matching sobre texto normalizado** (minúsculas, sin acentos) en
   `creative_bodies + link_titles + link_captions + page_name + bylines`.
   - Exacto por alias largo; para nombres cortos o ambiguos ("Claudia", "Maru") exigir
     coocurrencia con partido, cargo o entidad para reducir falsos positivos.
   - Registrar *dónde* apareció la mención (cuerpo vs. página vs. pagador): una mención
     en `bylines` implica financiamiento declarado; en el cuerpo, solo tema.
3. Un anuncio puede mencionar **varios actores** (típico en ataques comparativos):
   la tabla de salida es `anuncio × actor`, no una etiqueta única por anuncio.
4. **Medir cobertura**: % de anuncios sin ningún actor detectado; muestrear esos
   anuncios para ampliar el diccionario (los apodos surgen durante la campaña).

## 4. Postura: ¿el mensaje favorece o ataca?

**El análisis de sentimiento genérico no sirve como métrica principal.** Un anuncio
con tono muy negativo ("la delincuencia destruyó al estado") puede ser un ataque al
rival, es decir, *favorece* al patrocinador. Lo que importa no es el tono del texto
sino la **postura hacia cada actor mencionado** (*stance detection*):

> Para cada par (anuncio, actor): `FAVORECE` / `ATACA` / `NEUTRAL-INFORMATIVO`.

Estrategia recomendada, en tres capas:

1. **Corpus de oro (humano).** Etiquetar a mano una muestra estratificada
   (~500–1,000 pares, estratificada por gasto para que los anuncios caros estén bien
   representados) con **2 anotadores por par** y regla de desempate. Reportar acuerdo
   inter-anotador (Cohen's κ); si κ < 0.7, las definiciones de las etiquetas son
   ambiguas y hay que refinarlas antes de seguir.
2. **Clasificación con LLM.** Un prompt que reciba el texto del anuncio, el nombre de
   la página, el `bylines` y el actor objetivo, y devuelva postura + cita textual que
   la justifica. Con modelos actuales esto supera con claridad a léxicos de sentimiento
   en español y maneja ironía y ataques implícitos. **Validar contra el corpus de oro
   y publicar precisión/recall por clase**; corregir el prompt hasta estabilizar.
3. **Escala.** Clasificar todo el corpus con el LLM (los anuncios se repiten mucho:
   deduplicar por texto normalizado antes de clasificar reduce el costo ~10×).
   Alternativa sin LLM para volúmenes enormes: afinar un modelo tipo BETO/RoBERTuito
   con las etiquetas de las capas 1–2 como entrenamiento.

Reglas de oro:

- La postura se define **respecto al actor**, no al patrocinador: un mismo anuncio
  puede `ATACA` al actor A y `FAVORECE` al actor B.
- El **beneficiario inferido** de un ataque (el rival del atacado) solo se asigna en
  contiendas de pocos punteros y siempre etiquetado como *inferido*, nunca mezclado
  con favorabilidad explícita.
- Publicar siempre la matriz de confusión contra el corpus de oro. Sin esa validación,
  cualquier cifra agregada es indefendible frente a un desmentido.

## 5. Dinero: cuantificación honesta con datos en rangos

Meta solo publica rangos de gasto por anuncio. Metodología de agregación:

- Sumar **cotas**: gasto mínimo = Σ `spend_lower`, gasto máximo = Σ `spend_upper`.
  Toda cifra publicada se reporta como intervalo: *"entre $X y $Y MXN"*.
- El punto medio (`spend_mid`) sirve **solo para ordenar** (rankings, tamaños en
  gráficas), nunca como cifra citada.
- Cortes analíticos mínimos: por actor beneficiado (gasto en anuncios que lo
  favorecen + ataques a sus rivales), por página, por `bylines`, por entidad federativa
  (`delivery_by_region`), por semana, y por plataforma (Facebook vs. Instagram).
- Anuncios activos siguen acumulando gasto: correr `scrape --refresh` sobre el periodo
  antes de cualquier corte final.

## 6. El corazón del proyecto: gasto por terceros y "fuera de libros"

### 6.1 Tipología de páginas anunciantes

Clasificar cada `page_id` con gasto político en:

| Tipo | Criterio |
|---|---|
| **Oficial** | Página del candidato/partido (cotejo con cuentas registradas ante INE y verificación manual) |
| **Gubernamental** | Dependencias, gobiernos estatales/municipales (propaganda con recursos públicos en tiempos prohibidos es una falta en sí misma) |
| **Medio** | Medios de comunicación, incl. locales — el vehículo típico del gasto encubierto |
| **Tercero identificable** | Organizaciones, sindicatos, empresas, influencers con identidad pública |
| **Página gris** | Sin responsable identificable: "noticias", memes, "movimientos ciudadanos" |

### 6.2 Señales de propaganda encubierta (score por página)

Ninguna señal prueba nada por sí sola; juntas priorizan qué investigar a mano:

1. **Concentración de postura:** ≥80% del gasto de la página favorece a un solo actor
   (o ataca a un solo rival) siendo la página un "medio" o página gris.
2. **Pagador opaco:** `bylines` vacío, igual al nombre de la página, o una razón social
   no localizable en registros públicos.
3. **Desproporción:** gasto alto vs. página pequeña o recién creada.
4. **Coordinación de contenido:** el mismo texto (o casi: similitud por *shingles*/
   embeddings sobre texto normalizado) pautado por varias páginas distintas en fechas
   cercanas — huella de una operación centralizada.
5. **Coordinación temporal:** ráfagas de inicio de pauta el mismo día entre páginas sin
   relación aparente.
6. **Geografía dirigida:** `delivery_by_region` concentrado en la entidad de la
   contienda aunque la página se presente como nacional.

### 6.3 Anuncios no declarados como políticos

Barrer `--ad-type ALL` con los nombres del diccionario de actores como `search_terms`.
Los resultados que **no** aparecen en el barrido político son pauta sobre actores
políticos sin la etiqueta de Meta: no traen datos de gasto, pero documentar su
existencia (ID, página, texto, captura del `ad_snapshot_url`) ya es hallazgo publicable
y reportable a Meta/INE.

## 7. Cruce con fiscalización

- Comparar por candidato: intervalo de gasto observado en Ad Library vs. gasto en
  redes reportado ante el **INE** (Sistema Integral de Fiscalización, topes de campaña).
  Un mínimo observado (Σ cotas inferiores) **mayor** que lo reportado es una
  inconsistencia documentable con evidencia dura.
- Ojo con la ventana: la fiscalización cubre periodos de precampaña/campaña definidos
  por el calendario electoral; alinear las fechas de entrega del anuncio a esos cortes.
- Implementación: llenar `dictionaries/ine_fiscalizacion.csv` a mano desde el SIF
  (formato documentado en `politica_meta/ine.py`) y correr
  `python -m politica_meta ine`. La inconsistencia se marca SOLO cuando la cota
  inferior del universo atribuible (anuncios con el actor en `bylines`) supera lo
  reportado — nunca con la cota superior ni con el universo de menciones.

## 8. Ética y publicación

- Solo datos públicos de la API oficial; sin scraping de perfiles personales.
- Distinguir siempre **hecho** (esta página gastó $X–$Y en anuncios que favorecen a Z)
  de **inferencia** (el patrón sugiere coordinación). Las señales de §6.2 priorizan
  investigación humana; no son acusaciones automatizables.
- Conservar `raw` (JSON original) y capturas de `ad_snapshot_url` de todo lo que se
  publique: los anuncios pueden ser retirados después.
- Publicar la metodología, el diccionario de actores y las métricas de validación
  junto con el dashboard: la replicabilidad es la defensa del proyecto.

## 9. Salidas hacia el dashboard (Streamlit)

Tablas mínimas que este pipeline debe producir:

1. `ads` — la base del scraper (ya existe).
2. `ad_actor_stance` — anuncio × actor × postura × método (humano/LLM) × cita.
3. `pages` — tipología (§6.1) + señales (§6.2) por página.
4. `actor_spend_weekly` — intervalos de gasto por actor/semana/entidad, listos para
   graficar (con la identidad visual de Umbral).
