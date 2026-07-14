"""Invariantes del pipeline: si algo de esto falla, los datos publicados mienten."""

from __future__ import annotations

import json

import pytest

from politica_meta.actors import Actor, load_dictionary, _word_regex, normalize
from politica_meta.aggregates import ad_detail, canonical_mx_region, spend_by_page_region, reconcile
from politica_meta.signals import page_signals
from politica_meta.storage import AdStore, range_bounds


# --- storage -------------------------------------------------------------------


def test_range_bounds_open_bucket():
    assert range_bounds({"lower_bound": "100", "upper_bound": "199"}) == (100.0, 199.0)
    assert range_bounds({"lower_bound": "1000000"}) == (1000000.0, None)  # bucket abierto
    assert range_bounds(None) == (None, None)


# --- canonicalización de entidades ----------------------------------------------


@pytest.mark.parametrize("variant,canonical", [
    ("Distrito Federal", "Ciudad de México"),
    ("State of Mexico", "Estado de México"),
    ("Querétaro Arteaga", "Querétaro"),
    ("Michoacán de Ocampo", "Michoacán"),
    ("VERACRUZ DE IGNACIO DE LA LLAVE", "Veracruz"),
    ("sonora", "Sonora"),
])
def test_canonical_variants(variant, canonical):
    assert canonical_mx_region(variant) == canonical


def test_foreign_region_is_none():
    assert canonical_mx_region("Texas") is None
    assert canonical_mx_region("Unknown") is None
    assert canonical_mx_region(None) is None


# --- agregados: intervalos y asignación ------------------------------------------


@pytest.fixture
def store(tmp_path):
    s = AdStore(tmp_path / "test.sqlite")
    s.upsert_many([
        {
            "id": "ad1", "page_id": "p1", "page_name": "Noticias Test",
            "bylines": "Partido X", "ad_delivery_start_time": "2025-01-10",
            "spend": {"lower_bound": "100", "upper_bound": "199"},
            "ad_creative_bodies": ["Apoyo total al partido"],
            "delivery_by_region": [
                {"region": "Sonora", "percentage": "0.8"},
                {"region": "Texas", "percentage": "0.2"},
            ],
        },
        {
            # bucket abierto: sin cota superior → NULL debe propagarse
            "id": "ad2", "page_id": "p1", "page_name": "Noticias Test",
            "bylines": None, "ad_delivery_start_time": "2025-01-11",
            "spend": {"lower_bound": "1000000"},
            "ad_creative_bodies": ["Sin pagador declarado"],
            "delivery_by_region": [{"region": "Sonora", "percentage": "1.0"}],
        },
    ])
    yield s
    s.close()


def test_unbounded_upper_propagates(store):
    mx, nonmx, diag = spend_by_page_region(store)
    sonora = mx[mx["region"] == "Sonora"].iloc[0]
    assert bool(sonora["upper_unbounded"])
    assert sonora["spend_upper"] is None or sonora["spend_upper"] != sonora["spend_upper"]  # NULL/NaN
    # la cota inferior nunca se pierde: 100*0.8 + 1M*1.0
    assert sonora["spend_lower"] == pytest.approx(100 * 0.8 + 1_000_000)


def test_foreign_spend_separated_not_dropped(store):
    mx, nonmx, _ = spend_by_page_region(store)
    assert "Texas" in set(nonmx["region"])
    assert nonmx[nonmx["region"] == "Texas"]["spend_lower"].iloc[0] == pytest.approx(20.0)
    assert "Texas" not in set(mx["region"])


def test_reconciliation_clean(store):
    mx, nonmx, _ = spend_by_page_region(store)
    rec = reconcile(store, mx, nonmx)
    assert rec["page_discrepancies"] == []


def test_ad_detail_public_urls_no_token(store):
    df = ad_detail(store)
    assert len(df) == 2
    assert all(df["ad_url"].str.startswith("https://www.facebook.com/ads/library/?id="))
    assert not df.astype(str).apply(lambda c: c.str.contains("access_token")).any().any()
    regs = dict(pair.split(":") for pair in
                df[df["ad_id"] == "ad1"]["regions_mx"].iloc[0].split("|"))
    assert set(regs) == {"Sonora"}  # solo entidades canónicas; Texas queda fuera


def test_page_signals_thresholds(store):
    sig = page_signals(store)
    row = sig[sig["page_id"] == "p1"].iloc[0]
    assert row["pct_sin_pagador"] == pytest.approx(0.5)
    assert bool(row["perfil_de_medio"])  # "Noticias Test"
    assert row["pct_pagador_ajeno"] == pytest.approx(0.5)  # ad1 pagado por tercero
    assert row["senales_activas"] >= 2


# --- actores: reglas de ambigüedad ------------------------------------------------


def _actor(**kw):
    base = dict(actor_id="x", nombre="X", tipo="persona", partido="", cargo="",
                entidad="", safe=None, ambiguous=None, context=None)
    base.update(kw)
    return Actor(**base)


def test_safe_alias_matches_alone():
    a = _actor(safe=_word_regex(["claudia sheinbaum"]))
    texts = {"body": normalize("Apoyo a Claudia Sheinbaum"), "page": "", "bylines": ""}
    m = a.match(texts, " ".join(texts.values()))
    assert m and m["via_ambiguous"] == 0


def test_ambiguous_alias_requires_context():
    a = _actor(ambiguous=_word_regex(["pan"]), context=_word_regex(["accion nacional"]))
    sin_contexto = {"body": normalize("compra pan en la tienda"), "page": "", "bylines": ""}
    assert a.match(sin_contexto, sin_contexto["body"]) is None
    con_contexto = {"body": normalize("vota pan, accion nacional"), "page": "", "bylines": ""}
    m = a.match(con_contexto, con_contexto["body"])
    assert m and m["via_ambiguous"] == 1


def test_word_boundaries_no_substring():
    a = _actor(safe=_word_regex(["morena"]))
    texts = {"body": normalize("las morenas del mar"), "page": "", "bylines": ""}
    assert a.match(texts, texts["body"]) is None


def test_dictionary_loads(tmp_path):
    actors = load_dictionary("dictionaries/actores.csv")
    assert len(actors) >= 58
    ids = [a.actor_id for a in actors]
    assert len(ids) == len(set(ids)), "actor_id duplicado en el diccionario"


# --- stance: métricas de validación ----------------------------------------------


def test_stance_ad_text_dedup():
    from politica_meta.stance import _ad_text

    row = {"creative_bodies": json.dumps(["Hola mundo", "Hola mundo"]),
           "link_titles": json.dumps(["Hola mundo"])}
    assert _ad_text(row) == "Hola mundo"
