"""Dashboard de pauta política en Meta (México) — umbral_

Modo instrumento (oscuro). Lee las tablas de data/aggregates/ generadas por
`python -m politica_meta aggregate`. Todo gasto se muestra como intervalo
[cota inferior, cota superior]; nunca como cifra puntual.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# --- Tokens umbral_ (modo instrumento) — assets/tokens.json es la fuente ------
INK = "#EDF1F4"
BASE = "#101418"
PANEL = "#171C22"
BORDER = "#2A3138"
GRIDLINE = "#232A31"
BASELINE = "#3A434C"
MUTED = "#8B95A0"
CAPTION = "#5C6670"
SIGNAL = "#5FD4C4"

AGG_DIR = Path("data/aggregates")
FUENTE = "Fuente: Meta Ad Library API · umbral_ · CC BY 4.0"
NOTA_MODELADO = (
    "Los montos por entidad son estimaciones modeladas: el gasto de cada anuncio "
    "se prorratea según la distribución de impresiones (`delivery_by_region`) que "
    "publica Meta, no según gasto verificado por región."
)

st.set_page_config(
    page_title="Pauta política en Meta · umbral_",
    page_icon="assets/umbral-favicon.svg",
    layout="wide",
)

st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap');
    html, body, [class*="css"] { font-family: 'IBM Plex Sans', sans-serif; }
    h1, h2, h3 { font-family: 'Space Grotesk', sans-serif !important; font-weight: 500 !important; letter-spacing: -0.02em; }
    [data-testid="stMetricValue"] { font-family: 'IBM Plex Mono', monospace; font-weight: 500; font-size: 1.6rem; }
    [data-testid="stMetricLabel"] { color: #8B95A0; }
    .fuente { font-family: 'IBM Plex Mono', monospace; font-size: 12px; color: #5C6670;
              border-top: 1px solid #2A3138; padding-top: 6px; margin-top: -8px; }
    .u-wordmark { font-family: 'Space Grotesk', sans-serif; font-weight: 500; font-size: 1.1rem; }
    .u-wordmark span { color: #5FD4C4; }
    </style>
    """,
    unsafe_allow_html=True,
)


# --- Datos ---------------------------------------------------------------------


# cache_resource (no cache_data): cache_data des-serializa una copia completa de
# todas las tablas (~60 MB con ad_detail) en CADA rerun de CADA sesión, lo que
# dispara la memoria en Streamlit Cloud. Con cache_resource se comparte una sola
# instancia; ninguna vista muta los DataFrames cacheados (siempre .copy()/derivados).
@st.cache_resource(ttl=3600)
def load_tables() -> dict[str, pd.DataFrame]:
    tables = {}
    for name in ["spend_by_page", "spend_by_region", "spend_by_page_region",
                 "spend_by_month", "spend_by_page_month", "ad_detail", "page_signals"]:
        path = AGG_DIR / f"{name}.parquet"
        if path.exists():
            tables[name] = pd.read_parquet(path)
    return tables


def fmt_mxn(value: float | None, unbounded: bool = False) -> str:
    # Sin "$": Streamlit interpreta pares de $ como LaTeX en markdown/metrics.
    if unbounded or value is None or pd.isna(value):
        return "sin techo conocido"
    if value >= 1e6:
        return f"{value / 1e6:,.1f} M"
    if value >= 1e3:
        return f"{value / 1e3:,.0f} k"
    return f"{value:,.0f}"


def fmt_intervalo(lo: float, hi: float | None, unbounded: bool = False) -> str:
    if unbounded or hi is None or pd.isna(hi):
        return f"≥ {fmt_mxn(lo)} MXN"
    return f"{fmt_mxn(lo)} – {fmt_mxn(hi)} MXN"


def parse_regiones(compacto: str | None) -> list[tuple[str, float]]:
    """'Sonora:0.6234|Sinaloa:0.2000' → [("Sonora", 0.6234), …] (orden original,
    descendente por entrega). Formato definido en aggregates.ad_detail."""
    if not compacto or pd.isna(compacto):
        return []
    pares = []
    for parte in compacto.split("|"):
        nombre, _, pct = parte.rpartition(":")
        try:
            pares.append((nombre, float(pct)))
        except ValueError:
            continue
    return pares


def base_layout(fig: go.Figure, height: int = 380) -> go.Figure:
    fig.update_layout(
        height=height,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="IBM Plex Sans", color=INK, size=14),
        margin=dict(l=8, r=16, t=8, b=8),
        showlegend=False,
        hoverlabel=dict(bgcolor=PANEL, bordercolor=BORDER,
                        font=dict(family="IBM Plex Mono", color=INK, size=13)),
    )
    fig.update_xaxes(showgrid=False, linecolor=BASELINE,
                     tickfont=dict(family="IBM Plex Mono", size=12, color=CAPTION))
    fig.update_yaxes(gridcolor=GRIDLINE, zerolinecolor=BASELINE, linecolor="rgba(0,0,0,0)",
                     tickfont=dict(family="IBM Plex Mono", size=12, color=CAPTION))
    return fig


def chart_meta(titulo: str, subtitulo: str) -> None:
    st.markdown(f"### {titulo}")
    st.markdown(f"<span style='color:{MUTED}'>{subtitulo}</span>", unsafe_allow_html=True)


def fuente() -> None:
    st.markdown(f"<div class='fuente'>{FUENTE}</div>", unsafe_allow_html=True)


def banda_mensual(df: pd.DataFrame) -> go.Figure:
    """Serie temporal del intervalo de gasto: banda entre cotas (la incertidumbre
    es parte del dato, no decoración)."""
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["month"], y=df["spend_upper"], name="cota superior",
        line=dict(color=SIGNAL, width=1), mode="lines",
        hovertemplate="%{x}<br>cota superior: $%{y:,.0f}<extra></extra>"))
    fig.add_trace(go.Scatter(
        x=df["month"], y=df["spend_lower"], name="cota inferior",
        line=dict(color=SIGNAL, width=2), mode="lines",
        fill="tonexty", fillcolor="rgba(95,212,196,0.15)",
        hovertemplate="%{x}<br>cota inferior: $%{y:,.0f}<extra></extra>"))
    return base_layout(fig)


def barras_ranking(df: pd.DataFrame, label_col: str) -> go.Figure:
    """Barras horizontales por cota inferior; bigote hasta la cota superior.
    Solo el líder lleva el color señal."""
    d = df.iloc[::-1]  # plotly dibuja de abajo hacia arriba
    colors = [BASELINE] * len(d)
    if len(colors):
        colors[-1] = SIGNAL  # la barra de hasta arriba = mayor gasto
    err = (d["spend_upper"].fillna(d["spend_lower"]) - d["spend_lower"]).clip(lower=0)
    fig = go.Figure(go.Bar(
        x=d["spend_lower"], y=d[label_col], orientation="h",
        marker=dict(color=colors),
        error_x=dict(type="data", array=err, arrayminus=[0] * len(d),
                     color=MUTED, thickness=1.5, width=3),
        customdata=d["spend_upper"],
        hovertemplate="%{y}<br>$%{x:,.0f} – $%{customdata:,.0f} MXN<extra></extra>",
    ))
    fig.update_yaxes(gridcolor="rgba(0,0,0,0)")
    fig.update_xaxes(showgrid=True, gridcolor=GRIDLINE)
    return base_layout(fig, height=max(300, 26 * len(df) + 60))


# --- Página ----------------------------------------------------------------------

tablas = load_tables()
if not tablas:
    st.error(
        "No hay tablas agregadas en `data/aggregates/`. "
        "Corre primero:\n\n```\npython -m politica_meta scrape --start … --end …\n"
        "python -m politica_meta aggregate\n```"
    )
    st.stop()

col_logo, col_titulo = st.columns([1, 11])
with col_logo:
    st.image("assets/umbral-isotype-dark.svg", width=44)
with col_titulo:
    st.markdown("<div class='u-wordmark'>umbral<span>_</span></div>", unsafe_allow_html=True)
    st.title("Pauta política en Meta · México")

pages = tablas["spend_by_page"]
months = tablas["spend_by_month"]
page_region = tablas["spend_by_page_region"]

total_lo = pages["spend_lower"].sum()
total_unbounded = bool(pages["upper_unbounded"].any())
total_hi = None if total_unbounded else pages["spend_upper"].sum()

c1, c2, c3, c4 = st.columns(4)
c1.metric("Gasto total (intervalo)", fmt_intervalo(total_lo, total_hi, total_unbounded))
c2.metric("Anuncios", f"{int(pages['ads'].sum()):,}")
c3.metric("Páginas anunciantes", f"{len(pages):,}")
c4.metric("Periodo (inicio de entrega)", f"{months['month'].min()} → {months['month'].max()}")

tab_panorama, tab_entidad, tab_anunciante, tab_senales = st.tabs(
    ["Panorama", "Por entidad", "Por anunciante", "Señales"]
)

with tab_panorama:
    chart_meta(
        f"El gasto observable acumula {fmt_intervalo(total_lo, total_hi, total_unbounded)}"
        if total_unbounded
        else f"El gasto observable acumula entre {fmt_mxn(total_lo)} y {fmt_mxn(total_hi)} MXN",
        "Anuncios de política y temas sociales · México · por mes de inicio de entrega · "
        "la banda es el intervalo [cota inferior, cota superior] que publica Meta",
    )
    st.plotly_chart(banda_mensual(months), use_container_width=True)
    fuente()

    top15 = pages.head(15)
    chart_meta(
        f"{top15.iloc[0]['page_name']} encabeza el gasto del periodo",
        "15 páginas con mayor gasto (cota inferior) · el bigote llega a la cota superior",
    )
    st.plotly_chart(barras_ranking(top15, "page_name"), use_container_width=True)
    fuente()

with tab_entidad:
    estados = sorted(page_region["region"].unique())
    col_sel, col_n = st.columns([3, 1])
    estado = col_sel.selectbox("Entidad federativa", estados,
                               index=estados.index("Sonora") if "Sonora" in estados else 0)
    top_n = col_n.select_slider("Páginas", options=[10, 20, 30, 50], value=20)

    sub = (page_region[page_region["region"] == estado]
           .sort_values("spend_lower", ascending=False).head(top_n))
    lo, unb = sub["spend_lower"].sum(), bool(sub["upper_unbounded"].any())
    hi = None if unb else sub["spend_upper"].sum()
    chart_meta(
        f"En {estado}, las {len(sub)} páginas líderes concentran {fmt_intervalo(lo, hi, unb)}",
        f"Gasto asignado a {estado} · ordenado por cota inferior",
    )
    st.plotly_chart(barras_ranking(sub, "page_name"), use_container_width=True)
    st.markdown(f"<span style='color:{CAPTION};font-size:13px'>{NOTA_MODELADO}</span>",
                unsafe_allow_html=True)
    fuente()

    st.dataframe(
        sub[["page_name", "bylines", "spend_lower", "spend_upper", "ad_touches"]]
        .rename(columns={"page_name": "Página", "bylines": "Pagado por",
                         "spend_lower": "Gasto mín (MXN)", "spend_upper": "Gasto máx (MXN)",
                         "ad_touches": "Anuncios que tocan la entidad"}),
        use_container_width=True, hide_index=True,
    )

with tab_anunciante:
    opciones = pages["page_name"].fillna(pages["page_id"]).tolist()
    sel = st.selectbox("Página anunciante (ordenadas por gasto)", opciones)
    row = pages.iloc[opciones.index(sel)]
    page_id = row["page_id"]

    d1, d2, d3 = st.columns(3)
    d1.metric("Gasto (intervalo)",
              fmt_intervalo(row["spend_lower"], row["spend_upper"], bool(row["upper_unbounded"])))
    d2.metric("Anuncios", f"{int(row['ads']):,}")
    d3.metric("Pagado por", str(row["bylines"] or "—")[:40])

    pm = tablas["spend_by_page_month"]
    serie = pm[pm["page_id"] == page_id].sort_values("month")
    if len(serie) > 1:
        chart_meta("Gasto mensual de la página",
                   "Por mes de inicio de entrega · banda = intervalo publicado por Meta")
        st.plotly_chart(banda_mensual(serie), use_container_width=True)
        fuente()

    footprint = (page_region[page_region["page_id"] == page_id]
                 .sort_values("spend_lower", ascending=False).head(10))
    if not footprint.empty:
        chart_meta("Dónde entrega sus anuncios",
                   "10 entidades con mayor gasto asignado · estimación modelada")
        st.plotly_chart(barras_ranking(footprint, "region"), use_container_width=True)
        st.markdown(f"<span style='color:{CAPTION};font-size:13px'>{NOTA_MODELADO}</span>",
                    unsafe_allow_html=True)
        fuente()

    detalle = tablas.get("ad_detail")
    if detalle is not None:
        ads_pagina = detalle[detalle["page_id"] == page_id].copy()
        ads_pagina["_regiones"] = ads_pagina["regions_mx"].map(parse_regiones)
        estados_pagina = sorted({e for regs in ads_pagina["_regiones"] for e, _ in regs})

        chart_meta("Anuncios de la página",
                   "Un renglón por anuncio · gasto como intervalo publicado por Meta · "
                   "el enlace abre el anuncio en la Ad Library pública")
        estado_ads = st.selectbox(
            "Entidad federativa (anuncios con entrega en…)",
            ["Todas las entidades"] + estados_pagina,
        )

        if estado_ads != "Todas las entidades":
            ads_pagina["_pct"] = ads_pagina["_regiones"].map(
                lambda regs: dict(regs).get(estado_ads, 0.0))
            ads_pagina = ads_pagina[ads_pagina["_pct"] > 0].copy()
            ads_pagina["entidad"] = estado_ads
            ads_pagina["pct_entrega"] = ads_pagina["_pct"] * 100
            ads_pagina["alloc_lower"] = ads_pagina["spend_lower"] * ads_pagina["_pct"]
            ads_pagina["alloc_upper"] = ads_pagina["spend_upper"] * ads_pagina["_pct"]
        else:
            ads_pagina["entidad"] = ads_pagina["_regiones"].map(
                lambda regs: regs[0][0] if regs else "—")

        columnas = {
            "start_date": "Inicio",
            "entidad": "Entidad" if estado_ads != "Todas las entidades" else "Entidad principal",
            "page_name": "Anunciante",
            "snippet": "Anuncio (primer texto)",
            "spend_lower": "Gasto mín (MXN)",
            "spend_upper": "Gasto máx (MXN)",
        }
        if estado_ads != "Todas las entidades":
            columnas |= {
                "pct_entrega": f"% entrega en {estado_ads}",
                "alloc_lower": "Asignado mín (MXN)",
                "alloc_upper": "Asignado máx (MXN)",
            }
        columnas["ad_url"] = "Ver"

        ads_pagina = ads_pagina.sort_values("spend_lower", ascending=False)
        st.dataframe(
            ads_pagina[list(columnas)].rename(columns=columnas),
            column_config={
                "Ver": st.column_config.LinkColumn("Ver", display_text="Ad Library"),
                f"% entrega en {estado_ads}": st.column_config.NumberColumn(format="%.1f %%"),
                "Gasto mín (MXN)": st.column_config.NumberColumn(format="%.0f"),
                "Gasto máx (MXN)": st.column_config.NumberColumn(format="%.0f"),
                "Asignado mín (MXN)": st.column_config.NumberColumn(format="%.0f"),
                "Asignado máx (MXN)": st.column_config.NumberColumn(format="%.0f"),
            },
            use_container_width=True, hide_index=True,
        )
        nota = (f"{len(ads_pagina):,} anuncios · Gasto máx vacío = sin techo conocido "
                "(bucket superior abierto de Meta).")
        if estado_ads != "Todas las entidades":
            nota += f" Las columnas asignadas prorratean el intervalo por el % de entrega en {estado_ads}. {NOTA_MODELADO}"
        st.markdown(f"<span style='color:{CAPTION};font-size:13px'>{nota}</span>",
                    unsafe_allow_html=True)
        fuente()

    st.markdown(
        f"<span style='color:{MUTED}'>Ver los anuncios de esta página en la "
        f"<a href='https://www.facebook.com/ads/library/?active_status=all&ad_type=political_and_issue_ads"
        f"&country=MX&view_all_page_id={page_id}' style='color:{SIGNAL}'>Ad Library pública</a></span>",
        unsafe_allow_html=True,
    )

with tab_senales:
    senales = tablas.get("page_signals")
    if senales is None:
        st.info("Corre `python -m politica_meta aggregate --by signals` para generar esta tabla.")
    else:
        chart_meta(
            "Señales de pauta con opacidad",
            "Cada señal es un hecho derivado de los datos de Meta, no un veredicto: "
            "sirven para priorizar revisión editorial, nunca para acusar por sí solas",
        )
        st.markdown(
            f"""<span style='color:{CAPTION};font-size:13px'>
            <b>Señales:</b> ① ≥50% de anuncios sin "Pagado por" · ② ≥3 pagadores distintos
            (página intermediaria) · ③ ≥50% de anuncios pagados por un tercero distinto a la
            página · ④ nombre con perfil de medio informativo · ⑤ ≥20% del gasto entregado
            fuera de México.</span>""",
            unsafe_allow_html=True,
        )
        col_m, col_g = st.columns([1, 1])
        min_senales = col_m.select_slider("Señales activas (mínimo)", options=[1, 2, 3, 4, 5], value=2)
        min_gasto = col_g.select_slider(
            "Gasto mínimo (cota inferior, MXN)", options=[0, 10_000, 100_000, 500_000], value=10_000)

        vista = senales[(senales["senales_activas"] >= min_senales)
                        & (senales["spend_lower"] >= min_gasto)].copy()
        vista["pct_sin_pagador"] *= 100
        vista["pct_pagador_ajeno"] *= 100
        vista["pct_entrega_extranjera"] *= 100
        st.dataframe(
            vista[["page_name", "ads", "spend_lower", "spend_upper", "pct_sin_pagador",
                   "pagadores_distintos", "pct_pagador_ajeno", "perfil_de_medio",
                   "pct_entrega_extranjera", "senales_activas"]]
            .rename(columns={
                "page_name": "Página", "ads": "Anuncios",
                "spend_lower": "Gasto mín (MXN)", "spend_upper": "Gasto máx (MXN)",
                "pct_sin_pagador": "% sin pagador", "pagadores_distintos": "Pagadores",
                "pct_pagador_ajeno": "% pagador ajeno", "perfil_de_medio": "Perfil de medio",
                "pct_entrega_extranjera": "% entrega extranjera",
                "senales_activas": "Señales",
            }),
            column_config={
                "% sin pagador": st.column_config.NumberColumn(format="%.0f %%"),
                "% pagador ajeno": st.column_config.NumberColumn(format="%.0f %%"),
                "% entrega extranjera": st.column_config.NumberColumn(format="%.0f %%"),
                "Gasto mín (MXN)": st.column_config.NumberColumn(format="%.0f"),
                "Gasto máx (MXN)": st.column_config.NumberColumn(format="%.0f"),
            },
            use_container_width=True, hide_index=True,
        )
        st.markdown(
            f"<span style='color:{CAPTION};font-size:13px'>{len(vista):,} páginas con "
            f"≥{min_senales} señales y gasto ≥{min_gasto:,} MXN · Gasto máx vacío = sin techo "
            "conocido · % entrega extranjera vacío = sin datos de región.</span>",
            unsafe_allow_html=True,
        )
        fuente()
