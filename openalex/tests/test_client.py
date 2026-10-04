"""Tests for openalex_client.py: URL building, cursor paging, and retries.

No network access: `get()` is overridden for paging tests and
`urllib.request.urlopen` is patched for the retry tests.
"""

from __future__ import annotations

import json
import unittest
import urllib.error
from unittest import mock

from openalex_client import (
    API_ROOT,
    MAX_PER_PAGE,
    OpenAlexClient,
    OpenAlexError,
    chunked,
    normalize_id,
    validate_entity,
)


def make_page(ids, next_cursor=None, count=0):
    return {
        "results": [{"id": f"https://openalex.org/{i}"} for i in ids],
        "meta": {"count": count, "next_cursor": next_cursor},
    }


class RecordingClient(OpenAlexClient):
    """Client whose `get()` returns canned pages and records every request."""

    def __init__(self, pages):
        super().__init__()
        self.pages = list(pages)
        self.requests = []

    def get(self, path, params=None):
        self.requests.append({"path": path, "params": dict(params or {})})
        index = len(self.requests) - 1
        if index < len(self.pages):
            return self.pages[index]
        return {"results": [], "meta": {}}


class FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code, retry_after=None):
    headers = {} if retry_after is None else {"Retry-After": str(retry_after)}
    return urllib.error.HTTPError("https://api.openalex.org/works", code, "err", headers, None)


class IdHelperTests(unittest.TestCase):
    def test_normalize_full_iri(self):
        self.assertEqual(normalize_id("https://openalex.org/W123"), "W123")
        self.assertEqual(normalize_id("https://openalex.org/W123/"), "W123")

    def test_normalize_leaves_plain_ids_and_prefixes(self):
        self.assertEqual(normalize_id("W123"), "W123")
        self.assertEqual(normalize_id("doi:10.1/x"), "doi:10.1/x")
        self.assertEqual(normalize_id("  W123  "), "W123")
        self.assertEqual(normalize_id(""), "")

    def test_validate_entity(self):
        self.assertEqual(validate_entity("works"), "works")
        with self.assertRaises(OpenAlexError):
            validate_entity("bogus")


class UrlTests(unittest.TestCase):
    def test_params_skip_none_and_empty(self):
        client = OpenAlexClient()
        url = client._build_url("works", {"search": None, "filter": "x:1", "sort": ""})
        self.assertTrue(url.startswith(f"{API_ROOT}/works?"))
        self.assertIn("filter=x%3A1", url)
        self.assertNotIn("search", url)
        self.assertNotIn("sort", url)

    def test_mailto_and_api_key_are_added(self):
        url = OpenAlexClient(mailto="a@b.co", api_key="secret")._build_url("works")
        self.assertIn("mailto=a%40b.co", url)
        self.assertIn("api_key=secret", url)

    def test_get_single_keeps_doi_punctuation(self):
        client = OpenAlexClient()
        with mock.patch.object(client, "get", return_value={}) as fake:
            client.get_single("works", "doi:10.1038/nature12373")
        # `:` and `.` stay literal; only the DOI's `/` is percent-encoded, which
        # the API accepts (verified live against api.openalex.org).
        self.assertEqual(fake.call_args[0][0], "works/doi:10.1038%2Fnature12373")

    def test_get_single_accepts_full_iri(self):
        client = OpenAlexClient()
        with mock.patch.object(client, "get", return_value={}) as fake:
            client.get_single("works", "https://openalex.org/W123")
        self.assertEqual(fake.call_args[0][0], "works/W123")

    def test_get_subresource_keeps_slash_literal(self):
        # Regression: quoting the whole path produced `W1%2Fngrams`, which the API
        # resolved back to the parent work instead of erroring.
        client = OpenAlexClient()
        with mock.patch.object(client, "get", return_value={}) as fake:
            client.get_subresource("works", "W2741809807", "ngrams")
        self.assertEqual(fake.call_args[0][0], "works/W2741809807/ngrams")


class PagingTests(unittest.TestCase):
    def test_walks_every_page_via_cursor(self):
        client = RecordingClient(
            [
                make_page(["W1", "W2"], next_cursor="abc", count=3),
                make_page(["W3"], next_cursor=None, count=3),
            ]
        )
        ids = [r["id"] for r in client.search("works")]
        self.assertEqual(
            ids,
            ["https://openalex.org/W1", "https://openalex.org/W2", "https://openalex.org/W3"],
        )
        self.assertEqual([r["params"].get("cursor") for r in client.requests], ["*", "abc"])

    def test_stops_at_limit_without_extra_request(self):
        client = RecordingClient([make_page(["W1", "W2"], next_cursor="abc", count=99)])
        ids = list(client.search("works", limit=2))
        self.assertEqual(len(ids), 2)
        self.assertEqual(len(client.requests), 1)
        self.assertEqual(client.requests[0]["params"]["per_page"], 2)

    def test_per_page_is_clamped_to_api_maximum(self):
        client = RecordingClient([make_page(["W1"], count=1)])
        list(client.search("works", per_page=9999))
        self.assertEqual(client.requests[0]["params"]["per_page"], MAX_PER_PAGE)

    def test_sample_uses_page_one_and_no_cursor(self):
        client = RecordingClient([make_page(["W1"], count=1000)])
        list(client.search("works", sample=5, seed=42))
        params = client.requests[0]["params"]
        self.assertEqual(params["sample"], 5)
        self.assertEqual(params["seed"], 42)
        self.assertEqual(params["page"], 1)
        self.assertNotIn("cursor", params)

    def test_sample_respects_limit(self):
        client = RecordingClient([make_page(["W1", "W2", "W3", "W4", "W5"], count=1000)])
        self.assertEqual(len(list(client.search("works", sample=5, limit=2))), 2)

    def test_progress_callback_reports_totals(self):
        seen = []
        client = RecordingClient(
            [
                make_page(["W1", "W2"], next_cursor="abc", count=3),
                make_page(["W3"], next_cursor=None, count=3),
            ]
        )
        list(
            client.search(
                "works",
                progress=lambda fetched, total, pages: seen.append((fetched, total, pages)),
            )
        )
        self.assertEqual(seen, [(2, 3, 1), (3, 3, 2)])

    def test_unknown_entity_raises(self):
        with self.assertRaises(OpenAlexError):
            list(OpenAlexClient().search("bogus"))

    def test_empty_result_set_yields_nothing(self):
        client = RecordingClient([make_page([], next_cursor="abc", count=0)])
        self.assertEqual(list(client.search("works")), [])

    def test_filters_and_sort_are_forwarded(self):
        client = RecordingClient([make_page(["W1"], count=1)])
        list(
            client.search(
                "works",
                search="graphene",
                filters="is_oa:true",
                sort="cited_by_count:desc",
                select="id",
            )
        )
        params = client.requests[0]["params"]
        self.assertEqual(params["search"], "graphene")
        self.assertEqual(params["filter"], "is_oa:true")
        self.assertEqual(params["sort"], "cited_by_count:desc")
        self.assertEqual(params["select"], "id")


class CountAndGroupTests(unittest.TestCase):
    def test_count_reads_meta_count(self):
        client = RecordingClient([{"results": [], "meta": {"count": 42}}])
        self.assertEqual(client.count("works"), 42)

    def test_count_missing_meta_is_zero(self):
        client = RecordingClient([{"results": []}])
        self.assertEqual(client.count("works"), 0)

    def test_count_validates_entity(self):
        with self.assertRaises(OpenAlexError):
            OpenAlexClient().count("bogus")

    def test_group_by_returns_groups(self):
        client = RecordingClient([{"group_by": [{"key": "2018", "count": 5}]}])
        self.assertEqual(client.group_by("works", "publication_year")[0]["count"], 5)



class RetryTests(unittest.TestCase):
    def test_retries_on_429_then_succeeds(self):
        client = OpenAlexClient(max_retries=2)
        responses = [http_error(429, retry_after=0), FakeResponse({"ok": True})]
        with mock.patch("urllib.request.urlopen", side_effect=responses), mock.patch(
            "openalex_client.time.sleep"
        ) as sleeper:
            self.assertEqual(client.get("works/W1"), {"ok": True})
        self.assertEqual(sleeper.call_count, 1)

    def test_honours_retry_after_header(self):
        client = OpenAlexClient(max_retries=1)
        responses = [http_error(429, retry_after=7), FakeResponse({"ok": True})]
        with mock.patch("urllib.request.urlopen", side_effect=responses), mock.patch(
            "openalex_client.time.sleep"
        ) as sleeper:
            client.get("works/W1")
        self.assertEqual(sleeper.call_args[0][0], 7.0)

    def test_retries_on_503(self):
        client = OpenAlexClient(max_retries=1)
        responses = [http_error(503), FakeResponse({"ok": True})]
        with mock.patch("urllib.request.urlopen", side_effect=responses), mock.patch(
            "openalex_client.time.sleep"
        ):
            self.assertEqual(client.get("works/W1"), {"ok": True})

    def test_gives_up_after_max_retries(self):
        client = OpenAlexClient(max_retries=2)
        with mock.patch("urllib.request.urlopen", side_effect=http_error(500)), mock.patch(
            "openalex_client.time.sleep"
        ) as sleeper:
            with self.assertRaises(OpenAlexError):
                client.get("works/W1")
        # max_retries=2 -> 3 attempts -> 2 sleeps
        self.assertEqual(sleeper.call_count, 2)

    def test_does_not_retry_client_errors(self):
        client = OpenAlexClient(max_retries=3)
        with mock.patch("urllib.request.urlopen", side_effect=http_error(404)), mock.patch(
            "openalex_client.time.sleep"
        ) as sleeper:
            with self.assertRaises(OpenAlexError):
                client.get("works/W1")
        self.assertEqual(sleeper.call_count, 0)

    def test_retries_on_url_error(self):
        client = OpenAlexClient(max_retries=1)
        responses = [urllib.error.URLError("boom"), FakeResponse({"ok": True})]
        with mock.patch("urllib.request.urlopen", side_effect=responses), mock.patch(
            "openalex_client.time.sleep"
        ):
            self.assertEqual(client.get("works/W1"), {"ok": True})

    def test_sleep_option_delays_between_requests(self):
        client = OpenAlexClient(sleep=0.5)
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse({"ok": True})), mock.patch(
            "openalex_client.time.sleep"
        ) as sleeper:
            client.get("works/W1")
        self.assertEqual(sleeper.call_args[0][0], 0.5)


class ChunkedTests(unittest.TestCase):
    def test_batches_ids(self):
        self.assertEqual(list(chunked(["a", "b", "c"], 2)), [["a", "b"], ["c"]])

    def test_empty_input(self):
        self.assertEqual(list(chunked([], 2)), [])

    def test_exact_multiple(self):
        self.assertEqual(list(chunked(["a", "b"], 2)), [["a", "b"]])


if __name__ == "__main__":
    unittest.main()

