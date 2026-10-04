# OpenAlex CLI

A dependency-free Python command-line client for the [OpenAlex API](https://help.openalex.org/api/) —
search 250M+ scholarly works, authors, journals, institutions, topics and funders, then export to
CSV / JSON for analysis in Excel, pandas, VOSviewer, etc.

* **No install needed** — pure standard library (`urllib`, `csv`, `argparse`). Python 3.8+.
* Free API, **no key required**. Rate limits: 100k requests/day, 10/second.
* Automatic **cursor paging** (fetch far beyond the 10,000-record page limit), retry with
  backoff on 429/5xx, and UTF-8/Excel-safe CSV output.
* **Streams** results to disk, so `--limit 0` exports of millions of records do not run out of
  memory (a 50k-row write peaks under ~1 MB of Python heap).
* `harvest` writes a **Web-of-Science-shaped schema** that the notebooks in this repository can
  consume directly, with no column remapping.
* 128 offline tests (`python -m pytest openalex/tests`).

## Files

| File | Purpose |
| --- | --- |
| `openalex_cli.py` | Command-line interface (run this) |
| `openalex_client.py` | Reusable API client: paging, retries, filters, progress hook |
| `flatten.py` | Converts nested JSON into flat CSV rows; rebuilds abstracts; WoS schemas |
| `__init__.py` | Package marker + version (enables `python -m openalex.openalex_cli`) |
| `tests/` | Offline test suite — no network, no API key |

## Setup

Nothing to install. OpenAlex asks that you identify yourself to get into the faster
"polite pool" — set it once per shell:

```bat
set OPENALEX_MAILTO=you@example.com
```

Or pass `--mailto you@example.com` on any command. If you have a premium key, use
`--api-key` or `set OPENALEX_API_KEY=...`.

To avoid retyping it, create a `.openalexrc` in the repository root or your home directory:

```ini
# .openalexrc
mailto = you@example.com
api_key = optional-premium-key
```

`--mailto` / `--api-key` / `OPENALEX_MAILTO` / `OPENALEX_API_KEY` all take precedence over it.

## Commands

```bat
cd /d "g:\My Drive\tecknology prediction\openalex"
python openalex_cli.py --help

:: ...or run it from the repository root — the script folder is added to
:: sys.path automatically, so the sibling imports still resolve:
python "openalex\openalex_cli.py" --help
```

### search — the main workhorse
```bat
:: Top-cited open-access papers on a topic since 2020, exported to CSV
python openalex_cli.py search works -q "machine learning in medicine" ^
    -f "publication_year:>2020,is_oa:true" --sort cited_by_count:desc ^
    --limit 500 --abstract --csv out\ml_medicine.csv

:: Everything matching (no cap) — uses cursor paging
python openalex_cli.py search works -f "authorships.institutions.ror:04tabkc73,publication_year:2024" ^
    --limit 0 --csv out\sharif_2024.csv

:: Random reproducible sample of 100 records
python openalex_cli.py search works -q "photocatalysis" --sample 100 --seed 42 --csv out\sample.csv
```

### get — one record by ID, DOI, ORCID, ROR, ISSN
```bat
python openalex_cli.py get works doi:10.1038/nature12373 --abstract
python openalex_cli.py get authors orcid:0000-0002-1825-0097
python openalex_cli.py get sources issn:0028-0836
```

### count — how many records match (fast, one request)
```bat
python openalex_cli.py count works -f "authorships.institutions.country_code:ir,publication_year:2023"
```

### group-by — instant facets / bibliometric aggregations
```bat
python openalex_cli.py group-by works publication_year -f "authorships.institutions.country_code:ir"
python openalex_cli.py group-by works primary_topic.id -q "graphene" --csv out\topics.csv
python openalex_cli.py group-by works open_access.oa_status -f "publication_year:2024"
```

### autocomplete — resolve names to IDs quickly
```bat
python openalex_cli.py autocomplete institutions "sharif"
python openalex_cli.py autocomplete authors "hinton"
```

### author-works / cited-by / references — citation workflows
```bat
:: Full publication list of an author (name, ORCID, or A-id all work)
python openalex_cli.py author-works "geoffrey hinton" --limit 0 --sort cited_by_count:desc --csv out\hinton.csv

:: Who cites this paper?
python openalex_cli.py cited-by doi:10.1038/nature12373 --limit 200 --csv out\citing.csv

:: What does this paper cite?  (--limit caps OUTPUT rows, --max-refs caps lookups)
python openalex_cli.py references doi:10.1038/nature12373 --csv out\refs.csv
python openalex_cli.py references doi:10.1038/nature12373 --max-refs 50 --limit 20 --csv out\refs.csv
```

### harvest — export ready to feed the notebooks in this repo
Writes a **Web-of-Science-shaped** file so `co_occurrence_network_analysis/`, `NLP/NLP.ipynb`
and `SDG/SDG.ipynb` can read it with no column renaming. Abstracts are rebuilt by default
(needed by the NLP/SDG notebooks) and the schema is enforced, so the header is always identical.

```bat
:: 'short' schema (default) — the columns the notebooks expect:
:: Abstract, Authors, C3, DOI, Funding, Journal, Keywords, Year,
:: Addresses, Title, Cite, Subject_Category, Countries
python openalex_cli.py harvest works -q "digital twin" --since 2015 ^
    --require-abstract --limit 0 --csv out\digital_twin_wos.csv

:: 'wos' schema — long field names matching cleaned_combined_data.csv
:: (Article Title, Source Title, Author Keywords, Times Cited, WoS Core, UT (Unique WOS ID), ...)
python openalex_cli.py harvest works -q "graphene" --schema wos --limit 500 --csv out\graphene_wos.csv

:: Default output path is out\harvest.csv when --csv is omitted
python openalex_cli.py harvest works -q "perovskite solar cell" --limit 1000
```

> **Abstracts are not universal.** OpenAlex has no abstract for a large share of works, and the
> notebooks key off that column — so use `--require-abstract` to add the `has_abstract:true`
> filter and only pull works that actually have one.

## Output options (all commands)

| Flag | Effect |
| --- | --- |
| *(none)* | Pretty table preview in the console |
| `--csv PATH` | Flattened CSV (`utf-8-sig`, opens cleanly in Excel, Persian-safe) |
| `--xlsx PATH` | Excel workbook (needs the optional `openpyxl` package) |
| `--jsonl PATH` | Raw API records, one JSON object per line |
| `--json` | Raw JSON to stdout (pipe into `jq`/python), streamed |
| `--preview N` | How many rows to show in the console (default 20) |
| `--abstract` | Rebuild abstracts from the inverted index (works only) |
| `--limit N` | Max records; **`--limit 0` means all matches** |
| `--per-page N` | Page size (clamped to 200 with a warning) |
| `--sort FIELD:desc` | e.g. `cited_by_count:desc`, `publication_date:desc` |
| `--select a,b,c` | Ask the API for fewer fields **and keep only those columns** in CSV/XLSX |
| `--dedupe` | Drop duplicate records (OR-ed filters can repeat a work) |
| `--ascii` | Transliterate console output to ASCII (legacy Windows consoles) |
| `--since` / `--until` | Works only: date range, e.g. `--since 2015 --until 2020-06` |

### Connection & control flags (accepted before *or* after the command)

| Flag | Effect |
| --- | --- |
| `--mailto EMAIL` | Polite pool contact (also `OPENALEX_MAILTO`, or `.openalexrc`) |
| `--api-key KEY` | Premium key (also `OPENALEX_API_KEY`, or `.openalexrc`) |
| `--sleep SECONDS` | Extra delay between requests |
| `--timeout SECONDS` | Per-request timeout (default 30) |
| `--retries N` | Retries for 429/5xx/network errors (default 4) |
| `--quiet` | Silence progress and warnings on stderr |
| `--version` | Print the version |

**Exit codes:** `0` success · `2` API or usage error (message on stderr) · `130` interrupted.

> **`--select` keeps only the columns you ask for.** Previously it trimmed the API payload but
> still wrote all ~35 columns, filling the unrequested ones with `0`/`""` — so
> `--select id,title,cited_by_count` could report a citation count of `0` for a paper with 39,953
> citations. Now the output contains exactly the selected columns and nothing else. Both OpenAlex
> names (`display_name`) and flat names (`title`) are accepted.


## Filter cheat-sheet

Filters are comma-separated (**AND**); `|` inside a value means **OR**; `!` means NOT;
`>`/`<` work on numbers and dates.

```
publication_year:2024                      publication_year:>2020
from_publication_date:2024-01-01           to_publication_date:2024-12-31
is_oa:true                                 open_access.oa_status:gold|hybrid
type:article                               language:en
cited_by_count:>100                        is_retracted:false
authorships.institutions.country_code:ir   authorships.institutions.ror:04tabkc73
author.id:A5108093963                      author.orcid:0000-0002-1825-0097
primary_location.source.id:S137773608      primary_location.source.issn:0028-0836
primary_topic.id:T10036                    concepts.id:C41008148
cites:W2159974629                          cited_by:W2159974629
has_doi:true                               has_abstract:true
default.search:"quantum sensing"           title.search:graphene
abstract.search:nanothermometry             raw_affiliation_strings.search:Tehran
```

Full field lists: <https://docs.openalex.org/api-entities/works/filter-works>

## Use as a library

```python
from openalex_client import OpenAlexClient
from flatten import flatten_work, flatten_wos

client = OpenAlexClient(mailto="you@example.com")

for work in client.search("works", filters="author.id:A5108093963", limit=100):
    row = flatten_work(work, with_abstract=True)
    print(row["publication_year"], row["title"])

print(client.count("works", filters="publication_year:2024"))
print(client.group_by("works", "type", filters="publication_year:2024")[:5])

# Long exports: stream rows straight to disk and watch progress on stderr.
def progress(fetched, total, pages):
    print(f"{fetched}/{total} records", end="\r")

with open("out/all.csv", "w", newline="", encoding="utf-8-sig") as handle:
    import csv
    writer = None
    for work in client.search("works", filters="is_oa:true", progress=progress):
        row = flatten_work(work, with_abstract=True)
        if writer is None:
            writer = csv.DictWriter(handle, fieldnames=list(row))
            writer.writeheader()
        writer.writerow(row)

# WoS-shaped rows for the notebooks
row = flatten_wos(work, schema="short")   # or schema="wos"
```

## Tests

128 tests, fully offline — `get()` is stubbed with recorded payloads and
`urllib.request.urlopen` is patched for the retry logic. No network, no API key.

```bat
cd /d "g:\My Drive\tecknology prediction"
python -m pytest openalex\tests
:: or, with no third-party packages at all:
python -m unittest discover -s openalex\tests -t .
```

## Notes & etiquette

* Always send `--mailto`; anonymous traffic is throttled harder.
* Use `--select` and a sensible `--limit` for big pulls; add `--sleep 0.1` if you hit 429s.
* Large exports print progress to stderr and stream to disk — `--limit 0` is safe to run, but
  for the whole corpus prefer the OpenAlex full data snapshot over the API.
* On a legacy Windows console, non-Latin titles can look like `Metâ€¦`. The data is fine (UTF-8);
  add `--ascii` for console previews, or run `chcp 65001` first.
* **`ngrams` was removed.** OpenAlex no longer serves n-grams — `/ngrams` returns 404 and
  `works/<id>/ngrams` silently returns the parent work record. `OpenAlexClient.get_subresource()`
  remains available for other sub-resources.
* Data is CC0; please cite OpenAlex in publications.
