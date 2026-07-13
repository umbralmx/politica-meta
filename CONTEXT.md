# Project briefing: politica-meta

> Self-contained context document. Paste this into any AI model (or give it to a
> collaborator) so it can work on this project with zero prior knowledge.
> Operational manual: `README.md` · Analysis methodology: `METODOLOGIA.md` (Spanish).
> Last updated: 2026-07-13.

## 1. Who and why

**Jay Ballesteros** runs **Umbral** (https://umbralmx.github.io/), a Mexican
civic-tech organization that publishes public-accountability data products. An
existing reference product is https://desaparecidosmx.streamlit.app/ (a Streamlit
dashboard, in Spanish, about disappeared persons).

**The problem this project attacks:** every Mexican election features massive
political spending on Facebook and Instagram that authorities rarely hold
accountable because it is hard to trace. A common scheme: politicians pay local
media outlets or third-party pages ("news" pages, meme pages, fake citizen
movements) to push favorable messaging while keeping that spending off the books —
never reported to the INE (the electoral authority that audits campaign spending
against legal caps).

**End goal, in three phases:**
1. ✅ A scraper that downloads all public data about political ads in Mexico from
   the Meta Ad Library API (DONE, fully operational).
2. ✅ A written methodology to assess which paid messages favor which politicians
   and how much money is involved, with special focus on third-party/covert
   spending (DONE: `METODOLOGIA.md`).
3. ⬜ A public Streamlit dashboard, styled with Umbral's brand identity (PENDING —
   this is the likely next phase of work).

## 2. What exists in the repo

```
politica_meta/            # Python package (Python 3.13, venv at .venv/)
  client.py               # Ad Library API client: pagination, backoff, rate limits
  scraper.py              # resumable date-windowed sweeps
  storage.py              # SQLite (data/ads_mx.sqlite), upsert by ad id
  export.py               # export to CSV/Parquet
  aggregates.py           # joint page×region spend allocation + marginals + monthly
                          #   cohorts + reconciliation checks (see README for tables)
  __main__.py             # CLI (argparse)
README.md                 # setup + usage manual (Spanish)
METODOLOGIA.md            # analysis methodology (Spanish) — read it before analysis work
requirements.txt          # requests, python-dotenv, pandas, pyarrow
.env                      # META_ACCESS_TOKEN (gitignored; never commit)
data/                     # gitignored: SQLite DB, exports, aggregates
```

CLI (run from repo root with `.venv/bin/python`):

```bash
python -m politica_meta scrape --start 2026-01-01 --end 2026-06-30   # full MX sweep
python -m politica_meta stats
python -m politica_meta export --out data/ads_mx.parquet
python -m politica_meta aggregate --start 2026-01-01 --end 2026-06-30
```

## 3. Hard-won API facts (do not re-learn these the painful way)

- Endpoint: `GET https://graph.facebook.com/v25.0/ads_archive`. Docs:
  https://www.facebook.com/ads/library/api
- **Access chain (all completed for Jay's account):** Meta developer app (must be
  *active* — it once showed "API access deactivated" until reactivated from the app
  dashboard) → **identity confirmation** at facebook.com/ID (gate for ALL
  ads_archive queries; error code 10 until approved) → long-lived user token
  (current one expires ~2026-09-10; it was pasted in chat once, so rotating it is
  advisable; lives in `.env` as `META_ACCESS_TOKEN`).
- **Docs lie about empty searches:** the reference says `search_terms` is optional,
  but the live API returns error 100 ("search_terms and search_page_ids cannot be
  both empty"). The verified workaround is `search_terms="''"` (literal two single
  quotes), which acts as match-all. `client.py` injects this automatically.
- **Spend and impressions come ONLY as ranges** (e.g. $100–$499 MXN per ad), never
  exact figures. Every aggregate must be reported as an interval [Σ lower, Σ upper].
  This is a core methodological rule, not a nice-to-have.
- Mexico does NOT get the EU-only transparency fields (`target_ages`,
  `beneficiary_payers`, `eu_total_reach`, etc.). Available for MX political ads:
  `spend`, `impressions`, `estimated_audience_size`, `demographic_distribution`,
  `delivery_by_region`, `bylines` ("Paid for by" disclaimer), `currency`, creatives,
  `ad_snapshot_url`, page id/name, platforms, dates.
- **Deep pagination breaks** on large result sets (expired cursors, "reduce the
  amount of data" errors). Solution implemented: partition sweeps into 7-day
  delivery-date windows; each completed window is recorded in SQLite so re-running
  the same command resumes where it left off. The client also halves page size
  automatically when the API complains, and backs off exponentially on rate limits
  (error 613 etc.), pausing when the `x-app-usage` header nears 100%.
- Ads still delivering keep accumulating spend; `scrape --refresh` re-downloads
  completed windows to refresh ranges before any final analysis cut.
- `delivery_by_region` may include non-Mexican regions (ads reaching MX can deliver
  elsewhere too) — filter to the 32 entidades federativas for MX analysis.
- **The Ad Library Report** (facebook.com/ads/library/report — exact aggregate
  totals per advertiser/region since Aug 2020) has **no API**; it is manual CSV
  download only. Jay wants API-first for scalability, so `aggregates.py` derives
  interval-based equivalents from per-ad data. The official CSVs can serve as
  occasional ground truth.
- Environment quirk on Jay's Mac: `/etc/hosts` blocks `facebook.com` and
  `www.facebook.com` (0.0.0.0) — a distraction blocker. `graph.facebook.com` is NOT
  blocked, so the API works, but the report page / ad snapshots won't load in a
  local browser without lifting the block.

## 4. Proven working (2026-07-13)

A full one-day sweep (ads delivered 2026-07-06 in MX) returned **11,280 ads from
2,372 pages, spend interval $29.9M–$36.5M MXN**. The top-spender list validated the
project thesis instantly — it mixed official party pages (Movimiento Ciudadano,
Partido Verde), politicians (Jorge Álvarez Máynez), government agencies (IMSS,
Infonavit), media/entertainment pages running political content (Badabun), and
suspicious gray pages ("Red Transformación", "Pueblo que transforma", "Política Al
Día Novedades"). The first ad returned was flagged "This ad ran without a required
disclaimer."

## 5. Methodology in one paragraph (full version: METODOLOGIA.md)

Attribution before sentiment: build a hand-maintained **actor dictionary**
(candidates, parties, aliases) and match it against ad texts/page names/bylines.
Then classify **stance per (ad, actor) pair** — FAVORECE / ATACA / NEUTRAL — because
generic sentiment analysis fails here (a negative-tone ad is often an attack that
*favors* the sponsor). Recommended stack: human-labeled gold set (~500–1,000 pairs,
2 annotators, Cohen's κ ≥ 0.7) → LLM classification validated against it (publish
precision/recall) → scale with text-dedup. Money is always reported as intervals.
The covert-spending core: classify pages into official / government / media /
identifiable third party / **gray page**, score them on six signals (stance
concentration toward one actor, opaque bylines, spend disproportionate to page,
identical creatives across pages, coordinated launch timing, geographic targeting
mismatch), and cross-check observed spend intervals against what candidates
reported to INE — an observed lower bound exceeding the reported figure is
hard-evidence inconsistency. Always separate fact from inference; keep raw JSON and
ad snapshots as evidence; publish validation metrics.

## 6. Conventions and constraints for anyone (human or AI) continuing this work

- Public-facing docs and dashboard copy: **Spanish**. Code identifiers/comments:
  as-is (mixed, code English, comments Spanish is fine).
- Never publish point estimates of spend — intervals only.
- Never commit `.env` or `data/`.
- The eventual dashboard should use Umbral's brand identity (a `umbral-brand`
  Claude skill with official colors/logos exists in Jay's environment) and follow
  the style of desaparecidosmx.streamlit.app.
- Next concrete steps, in rough order:
  1. Scale the sweep (e.g. 2026 year-to-date; resumable, hours of runtime).
  2. Build the actor dictionary from INE candidate registries (§3 of methodology).
  3. Implement stance labeling pipeline (§4) and page typology + signals (§6).
  4. Streamlit dashboard reading the SQLite/Parquet outputs.
