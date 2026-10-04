"""Turn nested OpenAlex JSON records into flat rows suitable for CSV/Excel."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

SEP = "; "


def _get(record: Dict[str, Any], *path: str, default: Any = None) -> Any:
    node: Any = record
    for key in path:
        if not isinstance(node, dict):
            return default
        node = node.get(key)
        if node is None:
            return default
    return node


def _short_id(value: Optional[str]) -> str:
    """https://openalex.org/W123 -> W123 (keeps DOIs/ORCIDs untouched enough to be useful)."""
    if not value:
        return ""
    if value.startswith("https://openalex.org/"):
        return value.rsplit("/", 1)[-1]
    return value


def _join(values: Sequence[Any], limit: Optional[int] = None) -> str:
    cleaned = [str(v) for v in values if v not in (None, "")]
    if limit is not None and len(cleaned) > limit:
        cleaned = cleaned[:limit] + [f"...(+{len(cleaned) - limit})"]
    return SEP.join(cleaned)


def _uniquify(values: Iterable[Any]) -> List[str]:
    """Order-preserving, case-insensitive de-duplication of non-empty strings.

    Case-insensitive because OpenAlex mixes "Bibliometrics"/"bibliometrics" and
    such variants would otherwise become duplicate nodes in the keyword and
    institution co-occurrence networks. The first-seen spelling is kept.
    """
    seen: List[str] = []
    lowered: set = set()
    for value in values:
        text = str(value).strip() if value is not None else ""
        key = text.casefold()
        if text and key not in lowered:
            lowered.add(key)
            seen.append(text)
    return seen


def _abstract(record: Dict[str, Any]) -> str:
    """Rebuild the abstract from OpenAlex's inverted index."""
    index = record.get("abstract_inverted_index")
    if not isinstance(index, dict):
        return record.get("abstract") or ""
    positions: Dict[int, str] = {}
    for word, spots in index.items():
        for spot in spots or []:
            positions[int(spot)] = word
    return " ".join(positions[i] for i in sorted(positions))


# --------------------------------------------------------------------- per entity
def flatten_work(record: Dict[str, Any], with_abstract: bool = False) -> Dict[str, Any]:
    authorships: List[Dict[str, Any]] = record.get("authorships") or []
    authors = [_get(a, "author", "display_name", default="") for a in authorships]
    institutions: List[str] = []
    countries: List[str] = []
    for authorship in authorships:
        for inst in authorship.get("institutions") or []:
            name = inst.get("display_name")
            if name and name not in institutions:
                institutions.append(name)
        for code in authorship.get("countries") or []:
            if code and code not in countries:
                countries.append(code)

    corresponding = [
        _get(a, "author", "display_name", default="")
        for a in authorships
        if a.get("is_corresponding")
    ]

    row: Dict[str, Any] = {
        "id": _short_id(record.get("id")),
        "doi": record.get("doi") or "",
        "title": record.get("display_name") or record.get("title") or "",
        "publication_year": record.get("publication_year") or "",
        "publication_date": record.get("publication_date") or "",
        "type": record.get("type") or "",
        "language": record.get("language") or "",
        "cited_by_count": record.get("cited_by_count") or 0,
        "fwci": record.get("fwci") or "",
        "citation_percentile": _get(record, "citation_normalized_percentile", "value", default=""),
        "referenced_works_count": len(record.get("referenced_works") or []),
        "is_oa": _get(record, "open_access", "is_oa", default=""),
        "oa_status": _get(record, "open_access", "oa_status", default=""),
        "oa_url": _get(record, "open_access", "oa_url", default="") or "",
        "source": _get(record, "primary_location", "source", "display_name", default="") or "",
        "source_issn_l": _get(record, "primary_location", "source", "issn_l", default="") or "",
        "publisher": _get(record, "primary_location", "source", "host_organization_name", default="") or "",
        "volume": _get(record, "biblio", "volume", default="") or "",
        "issue": _get(record, "biblio", "issue", default="") or "",
        "first_page": _get(record, "biblio", "first_page", default="") or "",
        "last_page": _get(record, "biblio", "last_page", default="") or "",
        "authors_count": len(authorships),
        "authors": _join(authors, limit=25),
        "corresponding_authors": _join(corresponding, limit=10),
        "institutions": _join(institutions, limit=25),
        "countries": _join(countries),
        "primary_topic": _get(record, "primary_topic", "display_name", default="") or "",
        "primary_field": _get(record, "primary_topic", "field", "display_name", default="") or "",
        "topics": _join([t.get("display_name") for t in record.get("topics") or []], limit=5),
        "keywords": _join([k.get("display_name") for k in record.get("keywords") or []], limit=10),
        "concepts": _join([c.get("display_name") for c in record.get("concepts") or []], limit=8),
        "sdgs": _join([s.get("display_name") for s in record.get("sustainable_development_goals") or []]),
        "grants": _join(
            [
                f"{g.get('funder_display_name', '')} ({g.get('award_id') or 'n/a'})"
                for g in record.get("grants") or []
            ],
            limit=10,
        ),
        "is_retracted": record.get("is_retracted", ""),
        "landing_page": _get(record, "primary_location", "landing_page_url", default="") or "",
        "pdf_url": _get(record, "best_oa_location", "pdf_url", default="") or "",
    }
    if with_abstract:
        row["abstract"] = _abstract(record)
    return row


def flatten_author(record: Dict[str, Any]) -> Dict[str, Any]:
    affiliation = _get(record, "last_known_institutions", default=None) or []
    last_inst = affiliation[0] if affiliation else (record.get("last_known_institution") or {})
    return {
        "id": _short_id(record.get("id")),
        "name": record.get("display_name") or "",
        "alternatives": _join(record.get("display_name_alternatives") or [], limit=5),
        "orcid": record.get("orcid") or "",
        "works_count": record.get("works_count") or 0,
        "cited_by_count": record.get("cited_by_count") or 0,
        "h_index": _get(record, "summary_stats", "h_index", default=""),
        "i10_index": _get(record, "summary_stats", "i10_index", default=""),
        "2yr_mean_citedness": _get(record, "summary_stats", "2yr_mean_citedness", default=""),
        "last_institution": (last_inst or {}).get("display_name", "") or "",
        "last_institution_country": (last_inst or {}).get("country_code", "") or "",
        "affiliations": _join(
            [_get(a, "institution", "display_name", default="") for a in record.get("affiliations") or []],
            limit=10,
        ),
        "topics": _join([t.get("display_name") for t in record.get("topics") or []], limit=8),
        "works_api_url": record.get("works_api_url") or "",
    }


def flatten_source(record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": _short_id(record.get("id")),
        "name": record.get("display_name") or "",
        "type": record.get("type") or "",
        "issn_l": record.get("issn_l") or "",
        "issn": _join(record.get("issn") or []),
        "publisher": record.get("host_organization_name") or "",
        "country_code": record.get("country_code") or "",
        "is_oa": record.get("is_oa", ""),
        "is_in_doaj": record.get("is_in_doaj", ""),
        "apc_usd": _get(record, "apc_usd", default="") or "",
        "works_count": record.get("works_count") or 0,
        "cited_by_count": record.get("cited_by_count") or 0,
        "h_index": _get(record, "summary_stats", "h_index", default=""),
        "2yr_mean_citedness": _get(record, "summary_stats", "2yr_mean_citedness", default=""),
        "homepage_url": record.get("homepage_url") or "",
    }


def flatten_institution(record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": _short_id(record.get("id")),
        "name": record.get("display_name") or "",
        "ror": record.get("ror") or "",
        "type": record.get("type") or "",
        "country_code": record.get("country_code") or "",
        "city": _get(record, "geo", "city", default="") or "",
        "region": _get(record, "geo", "region", default="") or "",
        "works_count": record.get("works_count") or 0,
        "cited_by_count": record.get("cited_by_count") or 0,
        "h_index": _get(record, "summary_stats", "h_index", default=""),
        "homepage_url": record.get("homepage_url") or "",
    }


def flatten_generic(record: Dict[str, Any]) -> Dict[str, Any]:
    """Fallback: keep scalar fields, summarise the rest."""
    row: Dict[str, Any] = {}
    for key, value in record.items():
        if key in {"abstract_inverted_index", "counts_by_year", "international"}:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            row[key] = _short_id(value) if key == "id" and isinstance(value, str) else value
        elif isinstance(value, list):
            names = [v.get("display_name") for v in value if isinstance(v, dict) and v.get("display_name")]
            # Always a string, so generic rows never mix int and str in one column.
            row[key] = _join(names, limit=10) if names else str(len(value))
        elif isinstance(value, dict):
            row[key] = value.get("display_name") or ""
    return row


# `works` is intentionally absent: it is dispatched separately because it takes
# the extra `with_abstract` argument.
FLATTENERS = {
    "authors": flatten_author,
    "sources": flatten_source,
    "institutions": flatten_institution,
}


def flatten(entity: str, record: Dict[str, Any], with_abstract: bool = False) -> Dict[str, Any]:
    if entity == "works":
        return flatten_work(record, with_abstract=with_abstract)
    return FLATTENERS.get(entity, flatten_generic)(record)


# ===========================================================================
# Target schemas
# ===========================================================================
# Column names expected by the downstream notebooks in this repository:
#   * co_occurrence_network_analysis/README.md -> "Data Preparation" section
#   * NLP/NLP.ipynb  uses df['Abstract'] and df.Year
#   * SDG/SDG.ipynb  matches on Title / Abstract / Keywords
WOS_SHORT_COLUMNS: Tuple[str, ...] = (
    "Abstract",
    "Authors",
    "C3",
    "DOI",
    "Funding",
    "Journal",
    "Keywords",
    "Year",
    "Addresses",
    "Title",
    "Cite",
    "Subject_Category",
    "Countries",
)

# Long-form field names matching the WoS export header of
# `cleaned_combined_data.csv` at the repository root.
WOS_LONG_COLUMNS: Tuple[str, ...] = (
    "Article Title",
    "Authors",
    "Author Full Names",
    "Source Title",
    "Abstract",
    "Author Keywords",
    "Keywords Plus",
    "Addresses",
    "Affiliations",
    "Reprint Addresses",
    "Funding Orgs",
    "Funding Text",
    "Cited Reference Count",
    "Times Cited, WoS Core",
    "Times Cited, All Databases",
    "Publication Year",
    "Publication Date",
    "DOI",
    "Document Type",
    "Language",
    "Volume",
    "Issue",
    "Start Page",
    "End Page",
    "Article Number",
    "ISSN",
    "eISSN",
    "Publisher",
    "Research Areas",
    "WoS Categories",
    "Open Access Designations",
    "UT (Unique WOS ID)",
)

HARVEST_SCHEMAS: Dict[str, Tuple[str, ...]] = {
    "short": WOS_SHORT_COLUMNS,
    "wos": WOS_LONG_COLUMNS,
}


def _work_context(record: Dict[str, Any]) -> Dict[str, List[str]]:
    """Pull every authorship-derived list out of a work record in one pass."""
    authors: List[str] = []
    institutions: List[str] = []
    countries: List[str] = []
    addresses: List[str] = []
    raw_affiliations: List[str] = []
    corresponding: List[str] = []

    for authorship in record.get("authorships") or []:
        name = _get(authorship, "author", "display_name", default="") or ""
        if name:
            authors.append(name)
            if authorship.get("is_corresponding"):
                corresponding.append(name)
        for inst in authorship.get("institutions") or []:
            iname = (inst.get("display_name") or "").strip()
            code = (inst.get("country_code") or "").strip()
            if iname:
                institutions.append(iname)
                addresses.append(f"{iname}, {code}".strip(", "))
            elif code:
                addresses.append(code)
        for code in authorship.get("countries") or []:
            countries.append(code)
        for raw in authorship.get("raw_affiliation_strings") or []:
            raw_affiliations.append(raw)

    return {
        "authors": _uniquify(authors),
        "institutions": _uniquify(institutions),
        "countries": _uniquify(countries),
        "addresses": _uniquify(addresses),
        "raw_affiliations": _uniquify(raw_affiliations),
        "corresponding": _uniquify(corresponding),
    }



def flatten_wos(
    record: Dict[str, Any],
    schema: str = "short",
    with_abstract: bool = True,
) -> Dict[str, Any]:
    """Flatten a work into the WoS-shaped columns the notebooks expect.

    ``schema`` is either ``"short"`` (``Abstract``/``Authors``/``Year``/... as used
    by the network + NLP notebooks) or ``"wos"`` (long field names matching the
    WoS export header in ``cleaned_combined_data.csv``).
    """
    if schema not in HARVEST_SCHEMAS:
        raise ValueError(f"unknown schema '{schema}'; expected one of {', '.join(HARVEST_SCHEMAS)}")

    ctx = _work_context(record)
    abstract = _abstract(record) if with_abstract else ""
    title = record.get("display_name") or record.get("title") or ""
    journal = _get(record, "primary_location", "source", "display_name", default="") or ""
    year = record.get("publication_year") or ""

    keywords = _uniquify(k.get("display_name") for k in record.get("keywords") or [])
    concepts = _uniquify(c.get("display_name") for c in record.get("concepts") or [])
    topics = _uniquify(t.get("display_name") for t in record.get("topics") or [])
    research_area = _get(record, "primary_topic", "field", "display_name", default="") or ""
    funders = _uniquify(g.get("funder_display_name") for g in record.get("grants") or [])
    funding_text = _uniquify(
        f"{g.get('funder_display_name') or ''} {g.get('award_id') or ''}".strip()
        for g in record.get("grants") or []
    )
    categories = _uniquify([research_area] + topics)
    cited = record.get("cited_by_count")
    cited = cited if isinstance(cited, int) else 0

    if schema == "short":
        return {
            "Abstract": abstract,
            "Authors": SEP.join(ctx["authors"]),
            "C3": SEP.join(ctx["raw_affiliations"] or ctx["institutions"]),
            "DOI": record.get("doi") or "",
            "Funding": SEP.join(funding_text),
            "Journal": journal,
            "Keywords": SEP.join(_uniquify(keywords + concepts)),
            "Year": year,
            "Addresses": SEP.join(ctx["addresses"]),
            "Title": title,
            "Cite": cited,
            "Subject_Category": SEP.join(categories),
            "Countries": SEP.join(ctx["countries"]),
        }

    return {
        "Article Title": title,
        "Authors": SEP.join(ctx["authors"]),
        "Author Full Names": SEP.join(ctx["authors"]),
        "Source Title": journal,
        "Abstract": abstract,
        "Author Keywords": SEP.join(keywords),
        "Keywords Plus": SEP.join(concepts),
        "Addresses": SEP.join(ctx["addresses"]),
        "Affiliations": SEP.join(ctx["institutions"]),
        "Reprint Addresses": SEP.join(ctx["corresponding"]),
        "Funding Orgs": SEP.join(funders),
        "Funding Text": SEP.join(funding_text),
        "Cited Reference Count": len(record.get("referenced_works") or []),
        "Times Cited, WoS Core": cited,
        "Times Cited, All Databases": cited,
        "Publication Year": year,
        "Publication Date": record.get("publication_date") or "",
        "DOI": record.get("doi") or "",
        "Document Type": record.get("type") or "",
        "Language": record.get("language") or "",
        "Volume": _get(record, "biblio", "volume", default="") or "",
        "Issue": _get(record, "biblio", "issue", default="") or "",
        "Start Page": _get(record, "biblio", "first_page", default="") or "",
        "End Page": _get(record, "biblio", "last_page", default="") or "",
        "Article Number": _get(record, "biblio", "first_page", default="") or "",
        "ISSN": _get(record, "primary_location", "source", "issn_l", default="") or "",
        "eISSN": "",
        "Publisher": _get(record, "primary_location", "source", "host_organization_name", default="") or "",
        "Research Areas": research_area,
        "WoS Categories": SEP.join(categories),
        "Open Access Designations": _get(record, "open_access", "oa_status", default="") or "",
        "UT (Unique WOS ID)": _short_id(record.get("id")),
    }



# ===========================================================================
# `--select` projection
# ===========================================================================
# Maps OpenAlex API field names to the columns produced by `flatten_work`.
# Needed because `--select` trims the API payload while `flatten_work` always
# emits the full column set -- without this projection, unselected numeric
# fields would silently be written as 0.
WORK_SELECT_MAP: Dict[str, Tuple[str, ...]] = {
    "id": ("id",),
    "doi": ("doi",),
    "title": ("title",),
    "display_name": ("title",),
    "publication_year": ("publication_year",),
    "publication_date": ("publication_date",),
    "type": ("type",),
    "language": ("language",),
    "cited_by_count": ("cited_by_count",),
    "fwci": ("fwci",),
    "citation_normalized_percentile": ("citation_percentile",),
    "referenced_works": ("referenced_works_count",),
    "referenced_works_count": ("referenced_works_count",),
    "is_retracted": ("is_retracted",),
    "open_access": ("is_oa", "oa_status", "oa_url"),
    "best_oa_location": ("pdf_url",),
    "primary_location": ("source", "source_issn_l", "publisher", "landing_page"),
    "biblio": ("volume", "issue", "first_page", "last_page"),
    "authorships": (
        "authors_count",
        "authors",
        "corresponding_authors",
        "institutions",
        "countries",
    ),
    "primary_topic": ("primary_topic", "primary_field"),
    "topics": ("topics",),
    "keywords": ("keywords",),
    "concepts": ("concepts",),
    "sustainable_development_goals": ("sdgs",),
    "grants": ("grants",),
    "abstract_inverted_index": ("abstract",),
}


def project(
    row: Dict[str, Any],
    fields: Sequence[str],
    mapping: Optional[Dict[str, Tuple[str, ...]]] = None,
) -> Tuple[Dict[str, Any], List[str]]:
    """Keep only ``fields`` from a flattened row.

    Accepts either OpenAlex API names (``display_name``) or already-flattened
    column names (``title``). Returns ``(projected_row, unmatched_fields)`` so the
    caller can warn about selections that have no flat representation.
    """
    mapping = WORK_SELECT_MAP if mapping is None else mapping
    out: Dict[str, Any] = {}
    unmatched: List[str] = []
    for raw in fields:
        name = raw.strip()
        if not name:
            continue
        if name in row:
            out[name] = row[name]
            continue
        targets = [t for t in mapping.get(name, ()) if t in row]
        if targets:
            for target in targets:
                out[target] = row[target]
        else:
            unmatched.append(name)
    return out, unmatched

