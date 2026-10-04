"""Minimal OpenAlex API client (stdlib only).

Docs: https://help.openalex.org/api/  |  API root: https://api.openalex.org

Design notes
------------
* Uses urllib so there are no third-party dependencies.
* Sends a "polite pool" mailto (recommended by OpenAlex) via the `mailto`
  query param and the User-Agent header.
* Supports both classic `page` paging and `cursor` paging (needed for >10k rows).
* Retries on 429/5xx with exponential backoff and honours `Retry-After`.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional

API_ROOT = "https://api.openalex.org"
MAX_PER_PAGE = 200

# Entity endpoints exposed by the API.
ENTITIES = (
    "works",
    "authors",
    "sources",
    "institutions",
    "topics",
    "concepts",
    "publishers",
    "funders",
    "keywords",
)


class OpenAlexError(RuntimeError):
    """Raised when the API returns an unrecoverable error."""


def validate_entity(entity: str) -> str:
    """Return `entity` unchanged, or raise `OpenAlexError` if it is not an endpoint."""
    if entity not in ENTITIES:
        raise OpenAlexError(f"unknown entity '{entity}'; expected one of {', '.join(ENTITIES)}")
    return entity


def normalize_id(oid: str) -> str:
    """`https://openalex.org/W123` -> `W123`; anything else is stripped but kept."""
    oid = (oid or "").strip()
    if oid.startswith("http"):
        oid = oid.rstrip("/").rsplit("/", 1)[-1]
    return oid


def _error_body(exc: urllib.error.HTTPError, limit: int = 500) -> str:
    """Best-effort read of an HTTP error body.

    Guarded on purpose: if the body cannot be read (dropped connection, empty
    file object) an exception here would mask the real HTTP status code.
    """
    try:
        return exc.read().decode("utf-8", "replace")[:limit]
    except Exception:  # noqa: BLE001 - must never hide the status code
        return "<unreadable body>"


class OpenAlexClient:
    def __init__(
        self,
        mailto: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 30.0,
        max_retries: int = 4,
        sleep: float = 0.0,
    ) -> None:
        self.mailto = mailto
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.sleep = sleep  # extra delay between requests (be nice)

    # ---------------------------------------------------------------- low level
    def _build_url(self, path: str, params: Optional[Dict[str, Any]] = None) -> str:
        query: Dict[str, Any] = {k: v for k, v in (params or {}).items() if v not in (None, "")}
        if self.mailto:
            query["mailto"] = self.mailto
        if self.api_key:
            query["api_key"] = self.api_key
        url = f"{API_ROOT}/{path.lstrip('/')}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query, doseq=True)}"
        return url

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = self._build_url(path, params)
        ua = "openalex-cli/1.1 (+https://help.openalex.org/api/)"
        if self.mailto:
            ua += f" mailto:{self.mailto}"
        request = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "application/json"})

        delay = 1.0
        last_error: Optional[str] = None
        for attempt in range(self.max_retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if self.sleep:
                    time.sleep(self.sleep)
                return payload
            except urllib.error.HTTPError as exc:  # noqa: PERF203
                body = _error_body(exc)
                last_error = f"HTTP {exc.code} for {url}: {body}"
                retryable = exc.code == 429 or 500 <= exc.code < 600
                if not retryable or attempt == self.max_retries:
                    raise OpenAlexError(last_error) from exc
                wait = float(exc.headers.get("Retry-After") or delay)
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = f"{type(exc).__name__} for {url}: {exc}"
                if attempt == self.max_retries:
                    raise OpenAlexError(last_error) from exc
                wait = delay
            time.sleep(wait)
            delay = min(delay * 2, 30.0)
        raise OpenAlexError(last_error or "unknown error")

    # --------------------------------------------------------------- high level
    def get_single(self, entity: str, oid: str) -> Dict[str, Any]:
        """Fetch one record by OpenAlex ID, DOI, ORCID, ROR, ISSN, or `doi:`/`orcid:` prefix."""
        validate_entity(entity)
        return self.get(f"{entity}/{urllib.parse.quote(normalize_id(oid), safe=':.')}")

    def get_subresource(self, entity: str, oid: str, subresource: str) -> Dict[str, Any]:
        """Fetch a sub-resource such as `works/<id>/ngrams`.

        The id is quoted on its own so the `/` before the sub-resource stays
        literal. Quoting the whole path would encode it as `%2F` and silently
        collapse the request back to the parent record.

        Note: OpenAlex removed its `/ngrams` endpoint (it now returns the parent
        work), so this is a general-purpose helper rather than a feature hook.
        """
        validate_entity(entity)
        oid = urllib.parse.quote(normalize_id(oid), safe=":.")
        return self.get(f"{entity}/{oid}/{subresource.strip('/')}")

    def search(
        self,
        entity: str,
        search: Optional[str] = None,
        filters: Optional[str] = None,
        sort: Optional[str] = None,
        select: Optional[str] = None,
        per_page: int = MAX_PER_PAGE,
        limit: Optional[int] = None,
        sample: Optional[int] = None,
        seed: Optional[int] = None,
        progress: Optional[Callable[[int, int, int], None]] = None,
    ) -> Iterator[Dict[str, Any]]:
        """Yield records, transparently paging with a cursor.

        ``progress`` is an optional callable invoked after every page as
        ``progress(fetched, total, pages)``, where ``total`` is the server-side
        match count (0 if unknown). Handy for long exports.
        """
        validate_entity(entity)
        per_page = max(1, min(per_page, MAX_PER_PAGE))
        if limit is not None:
            per_page = min(per_page, limit)

        params: Dict[str, Any] = {
            "search": search,
            "filter": filters,
            "sort": sort,
            "select": select,
            "per_page": per_page,
        }
        if sample:
            # `sample` cannot be combined with cursor paging.
            params.update({"sample": sample, "seed": seed, "page": 1})
            page = self.get(entity, params)
            total = int((page.get("meta") or {}).get("count", 0) or 0)
            fetched = 0
            for record in page.get("results", [])[: limit or sample]:
                yield record
                fetched += 1
            if progress:
                progress(fetched, total, 1)
            return

        yielded = 0
        pages = 0
        total = 0
        cursor = "*"
        while cursor:
            params["cursor"] = cursor
            page = self.get(entity, params)
            pages += 1
            total = int((page.get("meta") or {}).get("count", total) or 0)
            results: List[Dict[str, Any]] = page.get("results", [])
            if not results:
                return
            for record in results:
                yield record
                yielded += 1
                if limit is not None and yielded >= limit:
                    return
            if progress:
                progress(yielded, total, pages)
            cursor = (page.get("meta") or {}).get("next_cursor")

    def count(self, entity: str, search: Optional[str] = None, filters: Optional[str] = None) -> int:
        validate_entity(entity)
        page = self.get(entity, {"search": search, "filter": filters, "per_page": 1})
        return int((page.get("meta") or {}).get("count", 0) or 0)

    def group_by(
        self,
        entity: str,
        group_by: str,
        search: Optional[str] = None,
        filters: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        validate_entity(entity)
        page = self.get(
            entity,
            {"search": search, "filter": filters, "group_by": group_by, "per_page": MAX_PER_PAGE},
        )
        return page.get("group_by", [])

    def autocomplete(self, entity: Optional[str], query: str) -> List[Dict[str, Any]]:
        path = f"autocomplete/{entity}" if entity else "autocomplete"
        return self.get(path, {"q": query}).get("results", [])


def chunked(items: Iterable[str], size: int = 50) -> Iterator[List[str]]:
    """Group ids so they can be OR-ed inside a single `filter=...|...` value."""
    batch: List[str] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch
