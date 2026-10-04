"""Shared fixtures: a realistic OpenAlex work record and a stubbed client."""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterator, List, Optional

# Trimmed but structurally faithful copy of a real OpenAlex works response.
SAMPLE_WORK: Dict[str, Any] = {
    "id": "https://openalex.org/W2741809807",
    "doi": "https://doi.org/10.7717/peerj.4375",
    "display_name": "The state of OA",
    "publication_year": 2018,
    "publication_date": "2018-02-13",
    "type": "article",
    "language": "en",
    "cited_by_count": 1260,
    "fwci": 95.2612,
    "citation_normalized_percentile": {"value": 0.99965, "is_in_top_1_percent": True},
    "referenced_works": ["https://openalex.org/W1", "https://openalex.org/W2"],
    "open_access": {
        "is_oa": True,
        "oa_status": "gold",
        "oa_url": "https://doi.org/10.7717/peerj.4375",
    },
    "best_oa_location": {"pdf_url": "https://example.org/paper.pdf"},
    "primary_location": {
        "landing_page_url": "https://doi.org/10.7717/peerj.4375",
        "source": {
            "display_name": "PeerJ",
            "issn_l": "2167-8359",
            "host_organization_name": "PeerJ, Inc.",
        },
    },
    "biblio": {"volume": "6", "issue": None, "first_page": "e4375", "last_page": "e4375"},
    "authorships": [
        {
            "author": {"display_name": "Heather Piwowar"},
            "is_corresponding": True,
            "countries": ["CA"],
            "institutions": [
                {"display_name": "University of Washington", "country_code": "US"}
            ],
            "raw_affiliation_strings": ["University of Washington, Seattle, USA"],
        },
        {
            "author": {"display_name": "Jason R Priem"},
            "is_corresponding": False,
            "countries": ["US", "CA"],
            "institutions": [
                {"display_name": "University of Washington", "country_code": "US"},
                {"display_name": "OpenAlex", "country_code": "US"},
            ],
            "raw_affiliation_strings": ["OpenAlex, USA"],
        },
    ],
    "primary_topic": {
        "display_name": "Scientometrics",
        "field": {"display_name": "Decision Sciences"},
    },
    "topics": [{"display_name": "Scientometrics"}, {"display_name": "Open Access"}],
    "keywords": [{"display_name": "open access"}, {"display_name": "bibliometrics"}],
    "concepts": [{"display_name": "Citation"}, {"display_name": "Bibliometrics"}],
    "sustainable_development_goals": [{"display_name": "Quality Education"}],
    "grants": [{"funder_display_name": "NSF", "award_id": "12345"}],
    "is_retracted": False,
    "abstract_inverted_index": {"Open": [0], "access": [1], "matters": [2]},
}


def sample_works(count: int = 3) -> List[Dict[str, Any]]:
    """`count` distinct work records derived from SAMPLE_WORK."""
    out: List[Dict[str, Any]] = []
    for index in range(count):
        record = dict(SAMPLE_WORK)
        record["id"] = f"https://openalex.org/W{index + 1}"
        record["display_name"] = f"Work {index + 1}"
        record["cited_by_count"] = (index + 1) * 10
        out.append(record)
    return out


class FakeClient:
    """Stands in for `OpenAlexClient` in CLI tests.

    Records every call so tests can assert on the filters/limits that were used.
    """

    def __init__(
        self,
        pages: Optional[List[List[Dict[str, Any]]]] = None,
        total: int = 0,
        records: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        self.pages = pages if pages is not None else []
        self.total = total
        self.records = records if records is not None else []
        self.calls: List[Dict[str, Any]] = []

    def count(self, entity: str, search: Optional[str] = None, filters: Optional[str] = None) -> int:
        self.calls.append({"method": "count", "entity": entity, "filters": filters})
        return self.total

    def search(self, entity: str, **kwargs: Any) -> Iterator[Dict[str, Any]]:
        self.calls.append({"method": "search", "entity": entity, **kwargs})
        progress: Optional[Callable[[int, int, int], None]] = kwargs.get("progress")
        limit = kwargs.get("limit")
        fetched = 0
        for page_index, page in enumerate(self.pages or [self.records], start=1):
            for record in page:
                if limit is not None and fetched >= limit:
                    return
                yield record
                fetched += 1
            if progress:
                progress(fetched, self.total, page_index)

    def get_single(self, entity: str, oid: str) -> Dict[str, Any]:
        self.calls.append({"method": "get_single", "entity": entity, "oid": oid})
        return dict(SAMPLE_WORK)

    def group_by(self, entity: str, group_by: str, search=None, filters=None):
        self.calls.append({"method": "group_by", "group_by": group_by})
        return [{"key": "2018", "key_display_name": "2018", "count": 5}]

    def autocomplete(self, entity: Optional[str], query: str):
        self.calls.append({"method": "autocomplete", "entity": entity, "query": query})
        return [
            {"id": "https://openalex.org/I133529467", "display_name": "Sharif University",
             "hint": "Tehran, Iran", "works_count": 48060, "cited_by_count": 1971226}
        ]
