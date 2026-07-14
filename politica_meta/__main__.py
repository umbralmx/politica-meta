"""CLI: python -m politica_meta {scrape,export,stats}"""

from __future__ import annotations

import argparse
import logging
import os
import sys

DEFAULT_DB = "data/ads_mx.sqlite"


def _load_token() -> str:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    token = os.environ.get("META_ACCESS_TOKEN", "")
    if not token:
        sys.exit(
            "Falta META_ACCESS_TOKEN. Crea un archivo .env (ver .env.example) "
            "o exporta la variable de entorno."
        )
    return token


def cmd_scrape(args: argparse.Namespace) -> None:
    from .client import AdLibraryClient
    from .scraper import run_sweep
    from .storage import AdStore

    client = AdLibraryClient(_load_token(), page_size=args.page_size)
    store = AdStore(args.db)
    query = {
        "countries": args.countries.split(","),
        "ad_type": args.ad_type,
        "active_status": "ALL",
    }
    if args.search_terms:
        query["search_terms"] = args.search_terms
    if args.page_ids:
        query["search_page_ids"] = args.page_ids.split(",")
    try:
        total, failed = run_sweep(
            client,
            store,
            start=args.start,
            end=args.end,
            window_days=args.window_days,
            refresh=args.refresh,
            **query,
        )
    finally:
        store.close()
    print(f"Listo: {total} anuncios descargados/actualizados en {args.db}")
    if failed:
        print(f"ATENCIÓN: {len(failed)} ventanas fallaron incluso divididas a 1 día:")
        for dmin, dmax, err in failed:
            print(f"  {dmin} → {dmax}: {err}")
        print("Vuelve a correr el mismo comando para reintentarlas.")
        sys.exit(1)


def cmd_export(args: argparse.Namespace) -> None:
    from .export import export
    from .storage import AdStore

    store = AdStore(args.db)
    try:
        n = export(store, args.out)
    finally:
        store.close()
    print(f"{n} anuncios exportados a {args.out}")


def cmd_aggregate(args: argparse.Namespace) -> None:
    from .aggregates import spend_by_page_region, top_pages_for_region, write_aggregates
    from .storage import AdStore

    store = AdStore(args.db)
    try:
        if args.region:
            mx, _, _ = spend_by_page_region(store, args.start, args.end)
            canon, top = top_pages_for_region(mx, args.region, args.top)
            cols = ["page_name", "page_id", "bylines", "spend_lower", "spend_upper", "upper_unbounded", "ad_touches"]
            print(f"Top {len(top)} páginas por gasto asignado en {canon} "
                  f"(intervalos MXN, modelado por delivery_by_region):")
            print(top[cols].to_string(index=False, max_colwidth=40, float_format=lambda x: f"{x:,.0f}"))
            import pathlib
            out = pathlib.Path(args.out_dir)
            out.mkdir(parents=True, exist_ok=True)
            slug = canon.lower().replace(" ", "_").replace("é", "e").replace("ó", "o").replace("í", "i")
            top.to_csv(out / f"top_pages_{slug}.csv", index=False)
            print(f"\nGuardado en {out / f'top_pages_{slug}.csv'}")
        else:
            counts = write_aggregates(store, args.out_dir, start=args.start, end=args.end, only=args.by)
            written = ", ".join(f"{k}={v}" for k, v in counts.items())
            print(f"Agregados escritos en {args.out_dir} (CSV + Parquet): {written}")
    finally:
        store.close()


def cmd_actors(args: argparse.Namespace) -> None:
    from .actors import actor_summary, export_matches, load_dictionary, match_all
    from .storage import AdStore

    actors = load_dictionary(args.dict)
    store = AdStore(args.db)
    try:
        stats = match_all(store, actors, start=args.start, end=args.end)
        n = export_matches(store, args.out_dir)
        summary = actor_summary(store)
    finally:
        store.close()
    print(
        f"Cobertura: {stats['ads_matched']:,}/{stats['ads_total']:,} anuncios "
        f"({stats['coverage']:.1%}) mencionan a algún actor del diccionario; "
        f"{stats['pairs']:,} pares anuncio×actor → {args.out_dir}/ad_actors.csv"
    )
    print("\nGasto de anuncios que mencionan a cada actor (no implica favorabilidad):")
    for _, r in summary.head(15).iterrows():
        hi = "sin techo" if r["upper_unbounded"] else f"${r['spend_upper']:,.0f}"
        print(
            f"  {r['actor_id']}: {int(r['ads']):,} anuncios, {int(r['pages']):,} páginas, "
            f"${r['spend_lower']:,.0f} – {hi}"
            + (f" · {int(r['ads_en_bylines'])} con mención en bylines" if r["ads_en_bylines"] else "")
        )


def cmd_stats(args: argparse.Namespace) -> None:
    from .storage import AdStore

    store = AdStore(args.db)
    try:
        s = store.stats()
    finally:
        store.close()
    print(f"Anuncios totales:   {s['total_ads']:,}")
    print(f"Páginas distintas:  {s['distinct_pages']:,}")
    print(f"Rango de entrega:   {s['delivery_from']} → {s['delivery_to']}")
    if s["spend_lower_sum"] is not None:
        print(
            f"Gasto (rango):      ${s['spend_lower_sum']:,.0f} – "
            f"${s['spend_upper_sum'] or 0:,.0f}"
        )
    if s["top_pages"]:
        print("\nTop páginas por gasto (cota superior):")
        for name, page_id, ads, lo, hi in s["top_pages"]:
            print(f"  {name or page_id}: {ads} anuncios, ${lo or 0:,.0f} – ${hi or 0:,.0f}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="politica_meta",
        description="Descarga anuncios políticos de la Meta Ad Library para México.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="logging detallado")
    sub = parser.add_subparsers(dest="command", required=True)

    p_scrape = sub.add_parser("scrape", help="descargar anuncios por rango de fechas")
    p_scrape.add_argument("--start", required=True, help="fecha inicial YYYY-MM-DD (por entrega)")
    p_scrape.add_argument("--end", required=True, help="fecha final YYYY-MM-DD")
    p_scrape.add_argument("--db", default=DEFAULT_DB, help=f"ruta SQLite (default: {DEFAULT_DB})")
    p_scrape.add_argument("--window-days", type=int, default=7, help="tamaño de ventana (default: 7)")
    p_scrape.add_argument("--countries", default="MX", help="códigos ISO separados por coma")
    p_scrape.add_argument(
        "--ad-type",
        default="POLITICAL_AND_ISSUE_ADS",
        choices=["POLITICAL_AND_ISSUE_ADS", "ALL"],
        help="ALL sirve para buscar propaganda no declarada como política",
    )
    p_scrape.add_argument("--search-terms", help="palabras clave (si se omite, descarga todo)")
    p_scrape.add_argument("--page-ids", help="IDs de páginas separados por coma (máx 10)")
    p_scrape.add_argument("--page-size", type=int, default=250, help="anuncios por petición")
    p_scrape.add_argument(
        "--refresh",
        action="store_true",
        help="re-descargar ventanas ya completadas (actualiza rangos de gasto)",
    )
    p_scrape.set_defaults(func=cmd_scrape)

    p_export = sub.add_parser("export", help="exportar la base a CSV/Parquet")
    p_export.add_argument("--db", default=DEFAULT_DB)
    p_export.add_argument("--out", default="data/ads_mx.csv", help="ruta .csv o .parquet")
    p_export.set_defaults(func=cmd_export)

    p_agg = sub.add_parser(
        "aggregate",
        help="tablas equivalentes al Ad Library Report (gasto por anunciante y por región)",
    )
    p_agg.add_argument("--db", default=DEFAULT_DB)
    p_agg.add_argument("--out-dir", default="data/aggregates")
    p_agg.add_argument("--start", help="filtrar por fecha de entrega mínima YYYY-MM-DD")
    p_agg.add_argument("--end", help="filtrar por fecha de entrega máxima YYYY-MM-DD")
    p_agg.add_argument(
        "--by",
        choices=["page", "region", "page_region", "month", "page_month", "ads"],
        help="emitir solo una familia de tablas (default: todas)",
    )
    p_agg.add_argument("--region", help='vista por entidad, p. ej. --region "Sonora"')
    p_agg.add_argument("--top", type=int, default=30, help="N páginas en la vista por entidad (default: 30)")
    p_agg.set_defaults(func=cmd_aggregate)

    p_act = sub.add_parser(
        "actors",
        help="empatar anuncios contra el diccionario de actores (metodología §3)",
    )
    p_act.add_argument("--db", default=DEFAULT_DB)
    p_act.add_argument("--dict", default="dictionaries/actores.csv")
    p_act.add_argument("--out-dir", default="data/aggregates")
    p_act.add_argument("--start", help="filtrar por fecha de entrega mínima YYYY-MM-DD")
    p_act.add_argument("--end", help="filtrar por fecha de entrega máxima YYYY-MM-DD")
    p_act.set_defaults(func=cmd_actors)

    p_stats = sub.add_parser("stats", help="resumen de lo descargado")
    p_stats.add_argument("--db", default=DEFAULT_DB)
    p_stats.set_defaults(func=cmd_stats)

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    args.func(args)


if __name__ == "__main__":
    main()
