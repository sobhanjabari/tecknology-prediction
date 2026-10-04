#!/usr/bin/env python
"""OpenAlex command-line client — search, export, and summarise scholarly data.

Docs for the underlying API: https://help.openalex.org/api/

Examples
--------
  python openalex_cli.py search works -q "machine learning in medicine" \
      --filter "publication_year:>2020,is_oa:true" --sort cited_by_count:desc \
      --limit 200 --csv works.csv

  # WoS-shaped export that the notebooks in this repo can consume directly
  python openalex_cli.py harvest works -q "digital twin" --since 2015 \
      --limit 0 --csv out/digital_twin_wos.csv

  python openalex_cli.py search authors -q "hinton" --limit 10
  python openalex_cli.py get works doi:10.1038/nature12373
  python openalex_cli.py count works --filter "authorships.institutions.country_code:ir"
  python openalex_cli.py group-by works publication_year --filter "concepts.id:C41008148"
  python openalex_cli.py autocomplete institutions "sharif"
  python openalex_cli.py author-works A5023888391 --csv my_papers.csv
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import os
import re
import sys
import time
import unicodedata
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Sequence

try:  # package mode: `python -m openalex.openalex_cli`
    from . import __version__
    from .flatten import (
        HARVEST_SCHEMAS,
        WORK_SELECT_MAP,
        flatten,
        flatten_wos,
        project,
    )
    from .openalex_client import (
        ENTITIES,
        MAX_PER_PAGE,
        OpenAlexClient,
        OpenAlexError,
        chunked,
    )
except ImportError:  # script mode: `python openalex/openalex_cli.py`
    from flatten import HARVEST_SCHEMAS, WORK_SELECT_MAP, flatten, flatten_wos, project
    from openalex_client import ENTITIES, MAX_PER_PAGE, OpenAlexClient, OpenAlexError, chunked

    __version__ = "1.1.0"

# Windows consoles default to cp1252, which explodes on non-Latin titles.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass


class CliError(RuntimeError):
    """Raised for user-facing problems: bad flag combinations, missing extras."""


# ------------------------------------------------------------------------ config
CONFIG_FILENAME = ".openalexrc"


def load_config(path: Optional[str] = None) -> Dict[str, str]:
    """Read `mailto` / `api_key` from an optional `.openalexrc` file.

    Looked up in the current directory first, then the home directory. The file
    is plain `key=value` lines; `#` starts a comment. This exists so the polite
    pool is used by default without retyping `--mailto` every time.
    """
    candidates = [path] if path else [
        os.path.join(os.getcwd(), CONFIG_FILENAME),
        os.path.join(os.path.expanduser("~"), CONFIG_FILENAME),
    ]
    for candidate in candidates:
        if not candidate or not os.path.isfile(candidate):
            continue
        config: Dict[str, str] = {}
        try:
            with open(candidate, encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    config[key.strip().lower()] = value.strip()
        except OSError:
            continue
        return config
    return {}


class Reporter:
    """Writes progress/info/warning messages to stderr; silenced by `--quiet`."""

    def __init__(self, quiet: bool = False) -> None:
        self.quiet = quiet

    def info(self, message: str) -> None:
        if not self.quiet:
            print(message, file=sys.stderr)

    def warn(self, message: str) -> None:
        if not self.quiet:
            print(f"warning: {message}", file=sys.stderr)


# ----------------------------------------------------------------------- output
# Typographic characters NFKD cannot decompose, mapped to ASCII equivalents.
_ASCII_MAP = {
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u2013": "-",
    "\u2014": "-",
    "\u2026": "...",
    "\u00a0": " ",
    "\u2022": "*",
    "\u2032": "'",
    "\u2033": '"',
    "\u00d7": "x",
}


def asciify(text: str) -> str:
    """Transliterate to ASCII so legacy Windows consoles don't show mojibake.

    Decomposes accents and drops the combining marks (`naive`, not `nai?ve`),
    and maps typographic punctuation to plain ASCII first.
    """
    for source, target in _ASCII_MAP.items():
        text = text.replace(source, target)
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return stripped.encode("ascii", "replace").decode("ascii")


def _cell(value: Any) -> Any:
    """Coerce a flattened value into something Excel can hold."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return json.dumps(value, ensure_ascii=False)


def write_csv(
    rows: Iterable[Dict[str, Any]],
    path: str,
    fields: Optional[Sequence[str]] = None,
    reporter: Optional[Reporter] = None,
) -> int:
    """Stream rows to CSV without holding the whole result set in memory.

    Flattening is deterministic, so every row shares the same keys and the header
    can be taken from the first row. Pass `fields` to force an explicit column
    order/schema.
    """
    reporter = reporter or Reporter()
    iterator = iter(rows)
    try:
        first = next(iterator)
    except StopIteration:
        reporter.warn("no results — nothing written")
        return 0

    fieldnames = list(fields) if fields else list(first.keys())
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    # utf-8-sig so Excel opens Persian/accented text correctly.
    with open(path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=fieldnames, extrasaction="ignore", restval=""
        )
        writer.writeheader()
        count = 0
        for row in itertools.chain((first,), iterator):
            writer.writerow(row)
            count += 1
    reporter.info(f"Wrote {count} rows -> {path}")
    return count


def write_xlsx(
    rows: Iterable[Dict[str, Any]],
    path: str,
    fields: Optional[Sequence[str]] = None,
    reporter: Optional[Reporter] = None,
) -> int:
    """Stream rows to .xlsx via the optional `openpyxl` package."""
    reporter = reporter or Reporter()
    try:
        from openpyxl import Workbook  # noqa: PLC0415  (optional dependency)
    except ImportError as exc:
        raise CliError(
            "--xlsx needs the optional 'openpyxl' package: pip install openpyxl"
        ) from exc

    iterator = iter(rows)
    try:
        first = next(iterator)
    except StopIteration:
        reporter.warn("no results — nothing written")
        return 0

    fieldnames = list(fields) if fields else list(first.keys())
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)

    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet("data")
    sheet.append(fieldnames)
    count = 0
    for row in itertools.chain((first,), iterator):
        sheet.append([_cell(row.get(name)) for name in fieldnames])
        count += 1
    workbook.save(path)
    reporter.info(f"Wrote {count} rows -> {path}")
    return count


def write_jsonl(
    records: Iterable[Dict[str, Any]],
    path: str,
    reporter: Optional[Reporter] = None,
) -> int:
    reporter = reporter or Reporter()
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    count = 0
    with open(path, "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            count += 1
    reporter.info(f"Wrote {count} records -> {path}")
    return count


def write_json_stream(records: Iterable[Dict[str, Any]], stream: Any) -> int:
    """Write a JSON array incrementally, so `--limit 0` never buffers everything."""
    count = 0
    stream.write("[")
    for record in records:
        stream.write(",\n" if count else "\n")
        stream.write(json.dumps(record, ensure_ascii=False, indent=2))
        count += 1
    stream.write("\n]\n" if count else "]\n")
    return count


def print_table(
    rows: List[Dict[str, Any]],
    columns: Optional[Sequence[str]] = None,
    width: int = 60,
    ascii_only: bool = False,
) -> None:
    if not rows:
        print("No results.")
        return
    columns = list(columns) if columns else list(rows[0].keys())[:6]

    def cell(value: Any) -> str:
        text = "" if value is None else str(value).replace("\n", " ")
        if ascii_only:
            text = asciify(text)
        if len(text) <= width:
            return text
        marker = "..." if ascii_only else "…"
        return text[: width - len(marker)] + marker

    widths = {c: max(len(c), *(len(cell(r.get(c))) for r in rows)) for c in columns}
    print(" | ".join(c.ljust(widths[c]) for c in columns))
    print("-+-".join("-" * widths[c] for c in columns))
    for row in rows:
        print(" | ".join(cell(row.get(c)).ljust(widths[c]) for c in columns))


PREVIEW_COLUMNS = {
    "works": ["id", "publication_year", "cited_by_count", "source", "title"],
    "authors": ["id", "name", "works_count", "cited_by_count", "h_index", "last_institution"],
    "sources": ["id", "name", "type", "publisher", "works_count", "is_oa"],
    "institutions": ["id", "name", "country_code", "works_count", "cited_by_count"],
}



# ------------------------------------------------------------------- transforms
def dedupe(records: Iterable[Dict[str, Any]]) -> Iterator[Dict[str, Any]]:
    """Drop records already seen, keyed on OpenAlex id (falling back to DOI).

    OR-ed filters (e.g. `references`) can return the same work more than once.
    Note this keeps a set of ids, so it costs memory proportional to the result.
    """
    seen: set = set()
    for record in records:
        ident = record.get("id") or record.get("doi") or json.dumps(record, sort_keys=True)
        if ident in seen:
            continue
        seen.add(ident)
        yield record


def make_progress(reporter: Reporter) -> Optional[Callable[[int, int, int], None]]:
    """Throttled stderr progress callback, so long exports don't look frozen."""
    if reporter.quiet:
        return None
    state = {"start": time.time(), "last": 0.0}

    def report(fetched: int, total: int, pages: int) -> None:
        now = time.time()
        if now - state["last"] < 1.0 and (not total or fetched < total):
            return
        state["last"] = now
        elapsed = max(now - state["start"], 1e-6)
        rate = fetched / elapsed
        prefix = f"  {fetched:,}/{total:,} records" if total else f"  {fetched:,} records"
        reporter.info(f"{prefix} · {pages} pages · {rate:,.0f}/s")

    return report


def normalize_date(value: str, upper: bool) -> str:
    """`2015` -> `2015-01-01` (lower) / `2015-12-31` (upper); `2015-06` -> `2015-06-01`/`30`."""
    value = value.strip()
    if len(value) == 4 and value.isdigit():
        return f"{value}-12-31" if upper else f"{value}-01-01"
    if len(value) == 7:
        return f"{value}-30" if upper else f"{value}-01"
    return value


def build_filters(args: argparse.Namespace, entity: str) -> Optional[str]:
    """Merge `--filter` with the `--since`/`--until` conveniences (works only)."""
    parts = [args.filter] if getattr(args, "filter", None) else []
    since = getattr(args, "since", None)
    until = getattr(args, "until", None)
    if since or until:
        if entity != "works":
            raise CliError("--since/--until only apply to the 'works' entity")
        if since:
            parts.append(f"from_publication_date:{normalize_date(since, upper=False)}")
        if until:
            parts.append(f"to_publication_date:{normalize_date(until, upper=True)}")
    return ",".join(p for p in parts if p) or None


def parse_select(args: argparse.Namespace) -> Optional[List[str]]:
    raw = getattr(args, "select", None)
    if not raw:
        return None
    return [field.strip() for field in raw.split(",") if field.strip()]


def resolve_per_page(args: argparse.Namespace, reporter: Reporter) -> int:
    """Clamp `--per-page` loudly instead of silently changing the request."""
    per_page = args.per_page
    if per_page < 1:
        raise CliError("--per-page must be at least 1")
    if per_page > MAX_PER_PAGE:
        reporter.warn(f"--per-page {per_page} exceeds the API maximum; using {MAX_PER_PAGE}")
        per_page = MAX_PER_PAGE
    return per_page


# ------------------------------------------------------------------------ emit
def _flatten_records(
    entity: str,
    records: Iterable[Dict[str, Any]],
    args: argparse.Namespace,
    selected: Optional[List[str]],
) -> Iterator[Dict[str, Any]]:
    """Turn raw API records into flat rows, applying `--select` projection."""
    reporter: Reporter = args._reporter
    with_abstract = getattr(args, "abstract", False)
    warned: List[str] = []

    for record in records:
        row = flatten(entity, record, with_abstract=with_abstract)
        if selected:
            mapping = WORK_SELECT_MAP if entity == "works" else {}
            row, missing = project(row, selected, mapping)
            fresh = [name for name in missing if name not in warned]
            if fresh:
                warned.extend(fresh)
                reporter.warn("--select fields with no flat column were omitted: " + ", ".join(fresh))
        yield row


def emit(
    entity: str,
    records: Iterable[Dict[str, Any]],
    args: argparse.Namespace,
    fields: Optional[Sequence[str]] = None,
) -> int:
    """Send results to CSV, XLSX, JSONL, raw JSON, or a console preview."""
    reporter: Reporter = args._reporter
    selected = parse_select(args)
    if args.dedupe:
        records = dedupe(records)

    # `--limit` must cap the output for every command. For `search`/`author-works`
    # the client already stops early, so this is a no-op there; for `references`
    # (which fans out over many OR-ed batches) it is the only cap.
    limit = getattr(args, "limit", None)
    if limit is not None:
        records = itertools.islice(records, limit)

    if args.jsonl:
        return write_jsonl(records, args.jsonl, reporter)
    if args.json:
        return write_json_stream(records, sys.stdout)

    rows = _flatten_records(entity, records, args, selected)

    if getattr(args, "xlsx", None):
        return write_xlsx(rows, args.xlsx, fields, reporter)
    if getattr(args, "csv", None):
        return write_csv(rows, args.csv, fields, reporter)

    preview = list(itertools.islice(rows, args.preview))
    print_table(preview, fields or PREVIEW_COLUMNS.get(entity), ascii_only=args.ascii)
    if len(preview) >= args.preview:
        print(f"... (showing {args.preview}; use --csv/--xlsx to export everything)")
    return len(preview)


def make_client(args: argparse.Namespace) -> OpenAlexClient:
    config = load_config()
    return OpenAlexClient(
        mailto=args.mailto or os.environ.get("OPENALEX_MAILTO") or config.get("mailto"),
        api_key=args.api_key or os.environ.get("OPENALEX_API_KEY") or config.get("api_key"),
        timeout=args.timeout,
        max_retries=args.retries,
        sleep=args.sleep,
    )



# --------------------------------------------------------------------- commands
def cmd_search(args: argparse.Namespace) -> int:
    reporter: Reporter = args._reporter
    client = make_client(args)
    filters = build_filters(args, args.entity)
    total = client.count(args.entity, args.query, filters)
    reporter.info(f"Matching {args.entity}: {total:,}")
    records = client.search(
        args.entity,
        search=args.query,
        filters=filters,
        sort=args.sort,
        select=args.select,
        per_page=resolve_per_page(args, reporter),
        limit=args.limit,
        sample=args.sample,
        seed=args.seed,
        progress=make_progress(reporter),
    )
    emit(args.entity, records, args)
    return 0


def cmd_get(args: argparse.Namespace) -> int:
    reporter: Reporter = args._reporter
    client = make_client(args)
    record = client.get_single(args.entity, args.id)
    if args.json or args.jsonl:
        return emit(args.entity, iter([record]), args)

    row = flatten(args.entity, record, with_abstract=getattr(args, "abstract", False))
    selected = parse_select(args)
    if selected:
        mapping = WORK_SELECT_MAP if args.entity == "works" else {}
        row, missing = project(row, selected, mapping)
        if missing:
            reporter.warn("--select fields with no flat column were omitted: " + ", ".join(missing))
    if getattr(args, "xlsx", None):
        write_xlsx([row], args.xlsx, list(row.keys()), reporter)
        return 0
    if args.csv:
        write_csv([row], args.csv, list(row.keys()), reporter)
        return 0

    width = max(len(key) for key in row)
    for key, value in row.items():
        text = str(value)
        print(f"{key.rjust(width)} : {asciify(text) if args.ascii else text}")
    return 0


def cmd_count(args: argparse.Namespace) -> int:
    filters = build_filters(args, args.entity)
    print(make_client(args).count(args.entity, args.query, filters))
    return 0


def cmd_group_by(args: argparse.Namespace) -> int:
    reporter: Reporter = args._reporter
    client = make_client(args)
    filters = build_filters(args, args.entity)
    groups = client.group_by(args.entity, args.group_by, args.query, filters)
    rows = [
        {"key": g.get("key_display_name") or g.get("key"), "count": g.get("count", 0)}
        for g in groups
    ]
    if args.json:
        write_json_stream(rows, sys.stdout)
        return 0
    if getattr(args, "xlsx", None):
        write_xlsx(rows, args.xlsx, ["key", "count"], reporter)
        return 0
    if args.csv:
        write_csv(rows, args.csv, ["key", "count"], reporter)
        return 0

    print_table(rows[: args.preview], ["key", "count"], ascii_only=args.ascii)
    if len(rows) > args.preview:
        print(f"... {len(rows) - args.preview} more rows (use --csv to export everything)")
    return 0


def cmd_autocomplete(args: argparse.Namespace) -> int:
    reporter: Reporter = args._reporter
    client = make_client(args)
    results = client.autocomplete(args.entity, args.query)
    rows = [
        {
            "id": (r.get("id") or "").rsplit("/", 1)[-1],
            "name": r.get("display_name", ""),
            "hint": r.get("hint", "") or "",
            "works_count": r.get("works_count", ""),
            "cited_by_count": r.get("cited_by_count", ""),
        }
        for r in results
    ]
    fields = ["id", "name", "works_count", "cited_by_count", "hint"]
    if args.json:
        write_json_stream(rows, sys.stdout)
        return 0
    if getattr(args, "xlsx", None):
        write_xlsx(rows, args.xlsx, fields, reporter)
        return 0
    if args.csv:
        write_csv(rows, args.csv, fields, reporter)
        return 0
    print_table(rows, fields, ascii_only=args.ascii)
    return 0


_ORCID_RE = re.compile(r"^\d{4}-\d{4}-\d{4}-[\dXx]{4}$")
_AUTHOR_ID_RE = re.compile(r"^A\d{6,}$")


def _resolve_author_id(client: OpenAlexClient, reporter: Reporter, author: str) -> str:
    """Accept an OpenAlex author id, an ORCID, or a free-text name."""
    author = author.strip()
    bare = author[6:] if author.lower().startswith("orcid:") else author
    if _ORCID_RE.match(bare):
        return f"orcid:{bare}"
    if _AUTHOR_ID_RE.match(author):
        return author

    matches = list(client.search("authors", search=author, limit=1))
    if not matches:
        raise CliError(f"no author matched '{author}'")
    found = matches[0]
    found_id = (found.get("id") or "").rsplit("/", 1)[-1]
    reporter.info(f"Resolved '{author}' -> {found.get('display_name')} ({found_id})")
    return found_id


def cmd_author_works(args: argparse.Namespace) -> int:
    """All works of an author, resolved from an OpenAlex ID, ORCID, or a name search."""
    reporter: Reporter = args._reporter
    client = make_client(args)
    author_id = _resolve_author_id(client, reporter, args.author)

    filters = f"author.id:{author_id}"
    if args.filter:
        filters += f",{args.filter}"
    total = client.count("works", None, filters)
    reporter.info(f"Works found: {total:,}")
    records = client.search(
        "works",
        filters=filters,
        sort=args.sort,
        per_page=resolve_per_page(args, reporter),
        limit=args.limit,
        progress=make_progress(reporter),
    )
    emit("works", records, args)
    return 0


def cmd_cited_by(args: argparse.Namespace) -> int:
    """Works that cite the given work (its incoming citations)."""
    reporter: Reporter = args._reporter
    client = make_client(args)
    work = client.get_single("works", args.work)
    work_id = (work.get("id") or "").rsplit("/", 1)[-1]
    reporter.info(f"Citations of: {work.get('display_name')} ({work_id})")
    records = client.search(
        "works",
        filters=f"cites:{work_id}",
        sort=args.sort,
        per_page=resolve_per_page(args, reporter),
        limit=args.limit,
        progress=make_progress(reporter),
    )
    emit("works", records, args)
    return 0


def cmd_references(args: argparse.Namespace) -> int:
    """Works referenced by the given work (its outgoing citations).

    `--limit` caps the number of output rows, consistent with every other
    command. `--max-refs` caps how many referenced ids are looked up at all.
    """
    reporter: Reporter = args._reporter
    client = make_client(args)
    work = client.get_single("works", args.work)
    work_id = (work.get("id") or "").rsplit("/", 1)[-1]
    referenced = [r.rsplit("/", 1)[-1] for r in work.get("referenced_works") or []]
    if not referenced:
        reporter.info("This record has no referenced_works in OpenAlex.")
        return 0
    if args.max_refs:
        referenced = referenced[: args.max_refs]
    reporter.info(f"Fetching {len(referenced)} references...")

    def fetch() -> Iterator[Dict[str, Any]]:
        for batch in chunked(referenced, 50):
            yield from client.search(
                "works", filters="openalex_id:" + "|".join(batch), per_page=50
            )

    emit("works", fetch(), args)
    return 0


def cmd_harvest(args: argparse.Namespace) -> int:
    """Export works in a Web-of-Science-shaped schema for this repo's notebooks.

    Two schemas are available:
      * `short` — `Abstract, Authors, C3, DOI, Funding, Journal, Keywords, Year,
        Addresses, Title, Cite, Subject_Category, Countries`, the columns the
        co-occurrence / NLP / SDG notebooks consume.
      * `wos` — long field names matching the WoS export header in
        `cleaned_combined_data.csv`.
    """
    reporter: Reporter = args._reporter
    client = make_client(args)
    filters = build_filters(args, "works")
    if args.require_abstract:
        # The notebooks in this repo key off the Abstract column, and OpenAlex
        # has no abstract for a sizeable share of works.
        filters = f"{filters},has_abstract:true" if filters else "has_abstract:true"
    total = client.count("works", args.query, filters)
    reporter.info(f"Matching works: {total:,}")

    records: Iterable[Dict[str, Any]] = client.search(
        "works",
        search=args.query,
        filters=filters,
        sort=args.sort,
        per_page=resolve_per_page(args, reporter),
        limit=args.limit,
        progress=make_progress(reporter),
    )
    if args.dedupe:
        records = dedupe(records)

    if args.jsonl:
        write_jsonl(records, args.jsonl, reporter)
        return 0
    if args.json:
        write_json_stream(records, sys.stdout)
        return 0

    args.abstract = not args.no_abstract
    fields = list(HARVEST_SCHEMAS[args.schema])
    rows = (
        flatten_wos(record, schema=args.schema, with_abstract=args.abstract)
        for record in records
    )
    if getattr(args, "xlsx", None):
        write_xlsx(rows, args.xlsx, fields, reporter)
    else:
        write_csv(rows, args.csv, fields, reporter)
    return 0


# ------------------------------------------------------------------------ parser
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openalex_cli.py",
        description="Query the OpenAlex API (https://help.openalex.org/api/) and export results.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    # Connection flags. They are also added to every sub-command with
    # `default=argparse.SUPPRESS`, so a value given *before* the command is not
    # overwritten by the sub-parser's own default.
    parser.add_argument("--mailto", help="Contact email for the polite pool (env: OPENALEX_MAILTO)")
    parser.add_argument("--api-key", help="Premium API key (env: OPENALEX_API_KEY)")
    parser.add_argument("--sleep", type=float, default=0.0, help="Seconds to wait between requests")
    parser.add_argument("--timeout", type=float, default=30.0, help="Per-request timeout in seconds (default 30)")
    parser.add_argument("--retries", type=int, default=4, help="Retries for 429/5xx/network errors (default 4)")
    parser.add_argument("--quiet", action="store_true", help="Suppress progress/warning output on stderr")

    def add_connection(sub: argparse.ArgumentParser) -> None:
        """Accept the connection flags after the sub-command too."""
        hidden = argparse.SUPPRESS
        sub.add_argument("--mailto", default=hidden, help="Contact email for the polite pool")
        sub.add_argument("--api-key", default=hidden, help="Premium API key")
        sub.add_argument("--sleep", type=float, default=hidden, help="Seconds between requests")
        sub.add_argument("--timeout", type=float, default=hidden, help="Per-request timeout in seconds")
        sub.add_argument("--retries", type=int, default=hidden, help="Retries for 429/5xx/network errors")
        sub.add_argument("--quiet", action="store_true", default=hidden, help="Suppress stderr progress")

    def add_output(
        sub: argparse.ArgumentParser,
        abstract: bool = False,
        selectable: bool = True,
    ) -> None:
        sub.add_argument("--csv", metavar="PATH", help="Write flattened rows to CSV")
        sub.add_argument("--xlsx", metavar="PATH", help="Write flattened rows to Excel (needs openpyxl)")
        sub.add_argument("--jsonl", metavar="PATH", help="Write raw JSON records, one per line")
        sub.add_argument("--json", action="store_true", help="Print raw JSON to stdout")
        sub.add_argument("--preview", type=int, default=20, help="Rows to show in console (default 20)")
        sub.add_argument("--dedupe", action="store_true", help="Drop duplicate records by id")
        sub.add_argument("--ascii", action="store_true", help="Transliterate console output to ASCII")
        if selectable:
            sub.add_argument("--select", help="Comma-separated fields to keep (smaller/faster responses)")
        if abstract:
            sub.add_argument("--abstract", action="store_true", help="Rebuild abstracts (works only)")

    def add_paging(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--limit", type=int, default=50, help="Max records to fetch (0 = all matches)")
        sub.add_argument("--per-page", type=int, default=MAX_PER_PAGE, help=f"Page size (<= {MAX_PER_PAGE})")
        sub.add_argument("--sort", help="e.g. cited_by_count:desc, publication_date:desc")

    def add_dates(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--since", metavar="DATE", help="Works only: published on/after (2015 or 2015-06)")
        sub.add_argument("--until", metavar="DATE", help="Works only: published on/before (2015 or 2015-06)")

    subparsers = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    p_search = subparsers.add_parser("search", help="Search/filter any entity")
    p_search.add_argument("entity", choices=ENTITIES)
    p_search.add_argument("-q", "--query", help="Full-text search string")
    p_search.add_argument("-f", "--filter", help='Comma-separated filters, e.g. "publication_year:>2020,is_oa:true"')
    p_search.add_argument("--sample", type=int, help="Return a random sample of N records")
    p_search.add_argument("--seed", type=int, help="Seed for --sample (reproducibility)")
    add_dates(p_search)
    add_paging(p_search)
    add_output(p_search, abstract=True)
    add_connection(p_search)
    p_search.set_defaults(func=cmd_search)

    p_harvest = subparsers.add_parser(
        "harvest", help="Export works in a WoS-shaped schema for this repo's notebooks"
    )
    # Optional `works` positional so `harvest works -q ...` reads naturally.
    p_harvest.add_argument(
        "entity",
        nargs="?",
        default="works",
        choices=["works"],
        help="Entity to harvest (only 'works' is supported; optional)",
    )
    p_harvest.add_argument("-q", "--query", help="Full-text search string")
    p_harvest.add_argument("-f", "--filter", help="Comma-separated filters")
    p_harvest.add_argument(
        "--schema",
        choices=sorted(HARVEST_SCHEMAS),
        default="short",
        help="'short' = notebook columns (default); 'wos' = long WoS field names",
    )
    p_harvest.add_argument("--no-abstract", action="store_true", help="Skip abstract rebuilding")
    p_harvest.add_argument(
        "--require-abstract",
        action="store_true",
        help="Only harvest works that actually have an abstract (filter has_abstract:true)",
    )
    add_dates(p_harvest)
    add_paging(p_harvest)
    add_output(p_harvest, selectable=False)
    add_connection(p_harvest)
    p_harvest.set_defaults(func=cmd_harvest, csv=os.path.join("out", "harvest.csv"))

    p_get = subparsers.add_parser("get", help="Fetch one record by ID/DOI/ORCID/ROR/ISSN")
    p_get.add_argument("entity", choices=ENTITIES)
    p_get.add_argument("id")
    add_output(p_get, abstract=True)
    add_connection(p_get)
    p_get.set_defaults(func=cmd_get)


    p_count = subparsers.add_parser("count", help="Print how many records match")
    p_count.add_argument("entity", choices=ENTITIES)
    p_count.add_argument("-q", "--query")
    p_count.add_argument("-f", "--filter")
    add_dates(p_count)
    add_connection(p_count)
    p_count.set_defaults(func=cmd_count)

    p_group = subparsers.add_parser("group-by", help="Aggregate counts by a field (facets)")
    p_group.add_argument("entity", choices=ENTITIES)
    p_group.add_argument("group_by", help="e.g. publication_year, type, authorships.institutions.country_code")
    p_group.add_argument("-q", "--query")
    p_group.add_argument("-f", "--filter")
    add_dates(p_group)
    add_output(p_group, selectable=False)
    add_connection(p_group)
    p_group.set_defaults(func=cmd_group_by)

    p_auto = subparsers.add_parser("autocomplete", help="Fast type-ahead lookup of entity names")
    p_auto.add_argument("entity", nargs="?", choices=ENTITIES, default=None)
    p_auto.add_argument("query")
    add_output(p_auto, selectable=False)
    add_connection(p_auto)
    p_auto.set_defaults(func=cmd_autocomplete)

    p_aw = subparsers.add_parser("author-works", help="Export every work of an author")
    p_aw.add_argument("author", help="OpenAlex author ID, ORCID, or a name to search")
    p_aw.add_argument("-f", "--filter", help="Extra filters ANDed with the author filter")
    add_paging(p_aw)
    add_output(p_aw, abstract=True)
    add_connection(p_aw)
    p_aw.set_defaults(func=cmd_author_works)

    p_cb = subparsers.add_parser("cited-by", help="List works citing a given work")
    p_cb.add_argument("work", help="Work ID or DOI")
    add_paging(p_cb)
    add_output(p_cb, abstract=True)
    add_connection(p_cb)
    p_cb.set_defaults(func=cmd_cited_by)

    p_ref = subparsers.add_parser("references", help="List works referenced by a given work")
    p_ref.add_argument("work", help="Work ID or DOI")
    p_ref.add_argument("--max-refs", type=int, help="Only look up the first N referenced works")
    add_paging(p_ref)
    add_output(p_ref, abstract=True)
    add_connection(p_ref)
    p_ref.set_defaults(func=cmd_references)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "limit", None) == 0:
        args.limit = None  # 0 means "no limit"
    args._reporter = Reporter(quiet=getattr(args, "quiet", False))
    try:
        return args.func(args)
    except (OpenAlexError, CliError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except BrokenPipeError:  # downstream `head`/`jq` closed the pipe
        return 0
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

