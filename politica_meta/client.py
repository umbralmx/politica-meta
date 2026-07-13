"""HTTP client for the Meta Ad Library API (ads_archive endpoint)."""

from __future__ import annotations

import json
import logging
import random
import time
from typing import Any, Iterator, Sequence

import requests

logger = logging.getLogger(__name__)

API_VERSION = "v25.0"
BASE_URL = f"https://graph.facebook.com/{API_VERSION}/ads_archive"

# Fields available for political ads outside the EU (Mexico included).
# EU-only fields (target_ages, beneficiary_payers, etc.) are intentionally excluded.
DEFAULT_FIELDS = [
    "id",
    "page_id",
    "page_name",
    "bylines",
    "currency",
    "spend",
    "impressions",
    "estimated_audience_size",
    "demographic_distribution",
    "delivery_by_region",
    "publisher_platforms",
    "languages",
    "ad_creation_time",
    "ad_delivery_start_time",
    "ad_delivery_stop_time",
    "ad_creative_bodies",
    "ad_creative_link_titles",
    "ad_creative_link_captions",
    "ad_creative_link_descriptions",
    "ad_snapshot_url",
]

# Graph API error codes that resolve by waiting and retrying (rate limits,
# transient server issues). 613 = ads_archive rate limit, 4/17/32 = app/user
# throttling, 1/2 = unknown/service errors.
TRANSIENT_ERROR_CODES = {1, 2, 4, 17, 32, 341, 613}

MIN_PAGE_SIZE = 25


class AdLibraryError(RuntimeError):
    """Non-retryable error returned by the Graph API."""

    def __init__(self, message: str, code: int | None = None, subcode: int | None = None):
        super().__init__(message)
        self.code = code
        self.subcode = subcode


class AdLibraryClient:
    """Paginating client with exponential backoff and adaptive page size.

    The Ad Library API throttles aggressively and sometimes rejects large
    pages with "please reduce the amount of data" — when that happens the
    page size is halved and the same cursor is retried.
    """

    def __init__(
        self,
        access_token: str,
        page_size: int = 250,
        max_retries: int = 8,
        timeout: int = 90,
        pause_between_pages: float = 0.3,
    ):
        if not access_token:
            raise ValueError("Se requiere un access token de Meta (META_ACCESS_TOKEN).")
        self.access_token = access_token
        self.page_size = page_size
        self.max_retries = max_retries
        self.timeout = timeout
        self.pause_between_pages = pause_between_pages
        self.session = requests.Session()

    def search(
        self,
        *,
        countries: Sequence[str] = ("MX",),
        ad_type: str = "POLITICAL_AND_ISSUE_ADS",
        active_status: str = "ALL",
        search_terms: str | None = None,
        search_page_ids: Sequence[str] | None = None,
        delivery_date_min: str | None = None,
        delivery_date_max: str | None = None,
        languages: Sequence[str] | None = None,
        publisher_platforms: Sequence[str] | None = None,
        media_type: str | None = None,
        fields: Sequence[str] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield every archived ad matching the query, across all pages.

        With no search_terms/search_page_ids the API returns ALL ads for the
        given countries and ad_type — that is the full-sweep mode.
        Dates are YYYY-MM-DD strings filtering by delivery date.
        """
        params: dict[str, Any] = {
            "access_token": self.access_token,
            "ad_reached_countries": json.dumps(list(countries)),
            "ad_type": ad_type,
            "ad_active_status": active_status,
            "fields": ",".join(fields or DEFAULT_FIELDS),
        }
        if search_terms:
            params["search_terms"] = search_terms
        if search_page_ids:
            params["search_page_ids"] = json.dumps(list(search_page_ids))
        if delivery_date_min:
            params["ad_delivery_date_min"] = delivery_date_min
        if delivery_date_max:
            params["ad_delivery_date_max"] = delivery_date_max
        if languages:
            params["languages"] = json.dumps(list(languages))
        if publisher_platforms:
            params["publisher_platforms"] = json.dumps(list(publisher_platforms))
        if media_type:
            params["media_type"] = media_type

        page_size = self.page_size
        after: str | None = None
        while True:
            params["limit"] = page_size
            if after:
                params["after"] = after
            payload, page_size = self._get(params, page_size)
            data = payload.get("data", [])
            yield from data
            paging = payload.get("paging", {})
            after = paging.get("cursors", {}).get("after")
            if not paging.get("next") or not after:
                break
            time.sleep(self.pause_between_pages)

    def _get(self, params: dict[str, Any], page_size: int) -> tuple[dict[str, Any], int]:
        """One request with retries. Returns (payload, possibly-reduced page size)."""
        for attempt in range(self.max_retries + 1):
            try:
                resp = self.session.get(BASE_URL, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                if attempt >= self.max_retries:
                    raise AdLibraryError(f"Error de red tras {attempt} reintentos: {exc}") from exc
                self._sleep(attempt, f"error de red: {exc}")
                continue

            self._throttle_if_near_limit(resp.headers)

            if resp.status_code == 200:
                return resp.json(), page_size

            error = self._parse_error(resp)
            code = error.get("code")
            message = error.get("message", resp.text[:500])

            if "reduce the amount of data" in message.lower() and page_size > MIN_PAGE_SIZE:
                page_size = max(MIN_PAGE_SIZE, page_size // 2)
                params["limit"] = page_size
                logger.warning("Respuesta demasiado grande; reduciendo page size a %d", page_size)
                continue

            retryable = code in TRANSIENT_ERROR_CODES or resp.status_code >= 500
            if retryable and attempt < self.max_retries:
                self._sleep(attempt, f"código {code}: {message}")
                continue

            raise AdLibraryError(
                f"Graph API error (HTTP {resp.status_code}, código {code}): {message}",
                code=code,
                subcode=error.get("error_subcode"),
            )
        raise AdLibraryError("Se agotaron los reintentos.")

    @staticmethod
    def _parse_error(resp: requests.Response) -> dict[str, Any]:
        try:
            return resp.json().get("error", {})
        except ValueError:
            return {}

    def _throttle_if_near_limit(self, headers: Any) -> None:
        """Slow down proactively when app usage reported by Meta is close to 100%."""
        usage_header = headers.get("x-app-usage")
        if not usage_header:
            return
        try:
            usage = json.loads(usage_header)
        except ValueError:
            return
        load = max(usage.get("call_count", 0), usage.get("total_time", 0), usage.get("total_cputime", 0))
        if load >= 90:
            logger.warning("Uso de la app al %d%%; pausando 60s para evitar bloqueo", load)
            time.sleep(60)

    @staticmethod
    def _sleep(attempt: int, reason: str) -> None:
        delay = min(300, 2**attempt * 5) + random.uniform(0, 3)
        logger.warning("Reintento %d en %.0fs (%s)", attempt + 1, delay, reason)
        time.sleep(delay)
