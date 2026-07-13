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
        total = run_sweep(
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


def cmd_export(args: argparse.Namespace) -> None:
    from .export import export
    from .storage import AdStore

    store = AdStore(args.db)
    try:
        n = export(store, args.out)
    finally:
        store.close()
    print(f"{n} anuncios exportados a {args.out}")


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
