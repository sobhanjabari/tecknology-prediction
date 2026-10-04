"""Web of Science parsing: tagged ``.txt`` exports and ``.csv`` exports.

Extracted from ``co_occurrence_network_analysis/`` (tagged parser) and
``SDG/SDG.ipynb`` (robust CSV loader), which each carried their own copy.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple, Union

import pandas as pd

__all__ = [
    "WOS_FIELD_MAP",
    "WOS_MULTILINE_TAGS",
    "WOS_COLUMNS",
    "WOS_EXPORT_MARKERS",
    "WOS_CSV_ALIASES",
    "parse_wos_file",
    "clean_c3_field",
    "extract_country_from_address",
    "extract_countries",
    "extract_organisation_from_address",
    "extract_organisations",
    "is_raw_wos_export",
    "normalize_wos_columns",
    "read_wos_csv",
    "save_to_excel",
    "process_wos_to_excel",
]

#: Two-letter WoS tag -> output column name.
WOS_FIELD_MAP: Dict[str, str] = {
    "PT": "start_new_record",
    "AB": "Abstract",
    "AF": "Authors",
    "TI": "Title",
    "SO": "Journal",
    "PY": "Year",
    "C1": "Addresses",
    "DI": "DOI",
    "DE": "Keywords",
    "TC": "Cite",
    "C3": "C3",
    "FU": "Funding",
    "WC": "Subject_Category",
}

#: Tags whose value continues on subsequent indented lines.
WOS_MULTILINE_TAGS = frozenset({"AF", "C3", "WC", "DE", "TI", "FU", "C1"})

#: Columns emitted by :func:`parse_wos_file`, in order.
#:
#: The original notebook also carried an ``ISI_Keywords`` column that was
#: initialised to ``""`` and never written to or read from; it is not emitted.
WOS_COLUMNS: Tuple[str, ...] = (
    "index",
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
)

#: How continuation lines are glued onto the existing value.
_CONTINUATION_JOINERS: Dict[str, str] = {
    "Authors": "|",
    "Addresses": "|",
    "C3": " ",
    "Subject_Category": " ",
    "Keywords": " ",
    "Title": " ",
    "Funding": " ",
}


def _new_record(position: int) -> Dict[str, str]:
    record = {column: "" for column in WOS_COLUMNS}
    record["index"] = position  # type: ignore[assignment]
    return record


def parse_wos_file(
    filepath: Union[str, Path],
    *,
    encoding: str = "utf-8",
) -> pd.DataFrame:
    """Parse a raw Web of Science tagged export into a :class:`pandas.DataFrame`.

    Records begin at the ``PT`` tag. Values that wrap onto indented lines are
    appended to the field that is currently open (see :data:`WOS_MULTILINE_TAGS`);
    ``Authors`` and ``Addresses`` are joined with ``|``, the rest with a space.

    Post-processing matches the notebooks: the trailing ``|`` is trimmed from
    ``Authors``, ``Journal`` is reduced to its first comma-separated segment,
    ``C3`` is passed through :func:`clean_c3_field`, and ``Countries`` is
    derived from ``Addresses`` via :func:`extract_countries`.

    Returns a DataFrame with the columns in :data:`WOS_COLUMNS` plus
    ``Countries``. A file with no ``PT`` records yields an empty DataFrame with
    those columns.
    """
    path = Path(filepath)
    with open(path, "r", encoding=encoding) as handle:
        lines = handle.readlines()

    records: List[Dict[str, str]] = []
    record: Optional[Dict[str, str]] = None
    current_field: Optional[str] = None

    for line in lines:
        if not line.strip():
            continue
        if len(line) < 3:
            continue

        prefix, content = line[:2], line[3:].strip()

        if prefix in WOS_FIELD_MAP:
            target = WOS_FIELD_MAP[prefix]
            if prefix == "PT":
                if record is not None:
                    records.append(record)
                record = _new_record(len(records) + 1)
                current_field = None
            elif record is not None:
                record[target] = content
                current_field = target if prefix in WOS_MULTILINE_TAGS else None
        elif line.startswith(" ") and record is not None:
            if current_field is not None:
                joiner = _CONTINUATION_JOINERS.get(current_field, " ")
                record[current_field] = record[current_field] + joiner + line.strip()
        else:
            current_field = None

    if record is not None:
        records.append(record)

    df = pd.DataFrame(records, columns=list(WOS_COLUMNS))
    if df.empty:
        df["Countries"] = pd.Series(dtype="object")
        return df

    df["Authors"] = df["Authors"].str.rstrip("|")
    df["Journal"] = df["Journal"].str.split(",").str[0]
    df["C3"] = df["C3"].apply(clean_c3_field)
    df["Countries"] = df["Addresses"].apply(extract_countries)
    return df


def clean_c3_field(
    c3_content: object,
    *,
    drop: Iterable[str] = (),
) -> str:
    """Drop empty items (and optional umbrella names) from a raw ``C3`` value.

    The notebooks kept a ``university_systems`` list of umbrella names to drop,
    but it was fully commented out, so the filter never actually ran. Pass the
    names via ``drop`` to enable it; the default preserves current behaviour.
    """
    if c3_content is None or (isinstance(c3_content, float) and pd.isna(c3_content)):
        return ""

    drop_set = {name.strip() for name in drop}
    items = [
        item.strip()
        for item in str(c3_content).split(";")
        if item.strip() and item.strip() not in drop_set
    ]
    return "; ".join(items)


def extract_country_from_address(address: object) -> str:
    """Return the normalised country of a single WoS address string.

    WoS address blocks are frequently wrapped in square brackets, e.g.
    ``[Piwowar H] Univ Washington, Seattle, WA 98195 USA``. US addresses are
    collapsed to ``USA`` and Turkish ones to ``Turkiye``.

    .. note::
       The original notebook implementation did ``parts[-1].strip()[:-1]``,
       blindly dropping the final character of every country. That turned
       ``"WA 98195 USA"`` into ``"WA 98195 US"``, so the ``USA`` and ``Turkey``
       normalisations below could never match. This version strips *brackets*
       around the country segment instead, which is what the slice was evidently
       reaching for, and rejects segments with no letters at all.
    """
    if address is None or (isinstance(address, float) and pd.isna(address)):
        return ""

    text = str(address).strip()
    if not text:
        return ""

    country = text.split(",")[-1].strip().strip("[]").strip()
    if not country or not any(character.isalpha() for character in country):
        return ""
    if "USA" in country:
        return "USA"
    if "Turkey" in country:
        return "Turkiye"
    return country


def extract_countries(addresses: object) -> str:
    """Extract one country per ``|``-separated address block, joined with ``" ; "``."""
    if addresses is None or (isinstance(addresses, float) and pd.isna(addresses)):
        return ""

    countries = [
        country
        for country in (extract_country_from_address(part) for part in str(addresses).split("|"))
        if country
    ]
    return " ; ".join(countries)


def extract_organisation_from_address(address: object) -> str:
    """Return the institution name from one WoS address block.

    Each block is prefixed with the authors it belongs to, wrapped in square
    brackets and full of commas - ``[Piwowar, Heather; Priem, Jason] Univ
    Washington, Seattle, WA 98195 USA``. The institution is whatever follows the
    closing bracket and precedes the first comma.
    """
    if address is None or (isinstance(address, float) and pd.isna(address)):
        return ""

    text = str(address).strip()
    if not text:
        return ""
    if text.startswith("["):
        closing = text.find("]")
        if closing != -1:
            text = text[closing + 1 :].strip()
    return text.split(",")[0].strip()


def extract_organisations(addresses: object) -> str:
    """Extract one institution per ``|``-separated address block, joined with ``"; "``."""
    if addresses is None or (isinstance(addresses, float) and pd.isna(addresses)):
        return ""

    names = [
        name
        for name in (
            extract_organisation_from_address(part) for part in str(addresses).split("|")
        )
        if name
    ]
    return "; ".join(names)


#: Raw exports separate address blocks with ``"; "`` rather than ``"|"``, and each
#: block starts with a bracketed author list. Splitting on a lookahead for ``[``
#: keeps each block whole, including the commas inside the brackets.
_ADDRESS_BLOCK_SPLIT = re.compile(r"\s*;\s*(?=\[)")

#: Headers that only ever appear in a raw Web of Science "Full Record" export.
WOS_EXPORT_MARKERS: Tuple[str, ...] = (
    "Publication Type",
    "WoS Categories",
    "UT (Unique WOS ID)",
    "Author Full Names",
)

#: Raw export header (or an already-correct one) per pipeline column, in
#: preference order. A WoS "Full Record" CSV names its columns ``Author
#: Keywords``, ``WoS Categories`` and ``Affiliations``, while the pipeline builds
#: its networks from ``Keywords``, ``Subject_Category`` and ``C3``.
WOS_CSV_ALIASES: Dict[str, Tuple[str, ...]] = {
    "Authors": ("Author Full Names", "Authors"),
    "Title": ("Article Title", "Title"),
    "Journal": ("Source Title", "Journal"),
    "Year": ("Publication Year", "Year"),
    "Keywords": ("Author Keywords", "Keywords Plus", "Keywords"),
    "Subject_Category": ("WoS Categories", "Subject_Category"),
    "C3": ("Affiliations", "C3"),
    "Funding": ("Funding Orgs", "Funding"),
    "DOI": ("DOI",),
    "Abstract": ("Abstract",),
    "Addresses": ("Addresses",),
}


def _join_people(value: object) -> str:
    """Re-join a ``;``-delimited author list with the pipeline's ``|``."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return "|".join(part.strip() for part in str(value).split(";") if part.strip())


def _join_address_blocks(value: object) -> str:
    """Re-join a raw export's ``;``-separated address blocks with ``"|"``."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return "|".join(
        block.strip() for block in _ADDRESS_BLOCK_SPLIT.split(str(value)) if block.strip()
    )


def is_raw_wos_export(data: pd.DataFrame) -> bool:
    """True when ``data`` looks like an untouched WoS "Full Record" export."""
    return any(marker in data.columns for marker in WOS_EXPORT_MARKERS)


def normalize_wos_columns(data: pd.DataFrame) -> pd.DataFrame:
    """Map a raw WoS export's headers onto the schema the pipeline builds from.

    :func:`read_wos_csv` returns whatever headers the file happens to carry. A WoS
    "Full Record" export uses long human-readable ones, so a real export loads
    without error and then builds only *one* of the five networks - and that one
    with every author list collapsed into a single node, because the export
    separates authors with ``;`` where the pipeline splits on ``|``.

    This renames the columns :data:`WOS_COLUMNS` expects, re-splits the author and
    address fields into the pipeline's delimiter, and derives the two columns a
    raw export never carries: ``Countries`` and ``C3`` (both from ``Addresses``).

    A column already named correctly is left alone, so the tagged and
    already-normalised paths are unaffected. Returns a new DataFrame; the input is
    not modified.
    """
    result = data.copy()
    result.columns = [str(column).strip() for column in result.columns]
    raw_export = is_raw_wos_export(result)

    for target, candidates in WOS_CSV_ALIASES.items():
        if target in result.columns:
            continue
        source = next((name for name in candidates if name in result.columns), None)
        if source is not None:
            result[target] = result[source]

    if raw_export:
        # The raw export delimits these with ";" where the pipeline expects "|".
        if "Authors" in result.columns:
            result["Authors"] = result["Authors"].map(_join_people)
        if "Addresses" in result.columns:
            result["Addresses"] = result["Addresses"].map(_join_address_blocks)

    if "Addresses" in result.columns:
        if "Countries" not in result.columns:
            result["Countries"] = result["Addresses"].map(extract_countries)
        if "C3" not in result.columns:
            result["C3"] = result["Addresses"].map(extract_organisations)

    return result


def read_wos_csv(
    path: Union[str, Path],
    *,
    expected_columns: Sequence[str] = (),
    encodings: Sequence[str] = ("utf-8", "utf-8-sig", "latin1"),
    sep: Optional[str] = ",",
) -> pd.DataFrame:
    """Load a WoS **CSV** export with encoding fallback and column validation.

    Adapted from ``SDG/SDG.ipynb``. Raises ``FileNotFoundError`` for a missing
    file, ``UnicodeDecodeError`` if no encoding works, ``ValueError`` for an
    empty file or when none of ``expected_columns`` is present. Missing-but-not-
    fatal columns are reported via ``warnings.warn``.

    ``sep`` is the column delimiter. Web of Science also exports tab-delimited
    data with a ``.txt`` extension, so :func:`techintel.pipeline.load_records`
    passes ``"\\t"`` for those. Pass ``None`` to let pandas sniff the delimiter
    (slower - it uses the Python engine).
    """
    import warnings

    csv_path = Path(path)
    if not csv_path.exists():
        raise FileNotFoundError(
            f"Input file not found: {csv_path}\n"
            f"Please place your WoS export at this location (or update the path)."
        )

    read_kwargs: Dict[str, object] = {"sep": sep}
    if sep is None:
        read_kwargs["engine"] = "python"
    else:
        # The chunked reader infers dtypes per chunk, so a large WoS export -
        # where a page-number or ID column is numeric for thousands of rows and
        # then holds "N/A" - ends up with mixed types, a ``DtypeWarning``, and a
        # column whose dtype depends on how the file happened to be split. This
        # file is a single export, not a stream, so reading it whole is fine.
        read_kwargs["low_memory"] = False

    data: Optional[pd.DataFrame] = None
    last_error: Optional[Exception] = None
    for encoding in encodings:
        try:
            data = pd.read_csv(csv_path, encoding=encoding, **read_kwargs)  # type: ignore[arg-type]
            break
        except UnicodeDecodeError as exc:  # pragma: no cover - depends on file bytes
            last_error = exc
            continue

    if data is None:
        raise last_error if last_error is not None else ValueError("Could not read file")

    if data.empty:
        raise ValueError(f"The input file '{csv_path.name}' was loaded but contains no rows.")

    expected = list(expected_columns)
    if expected:
        available = [column for column in expected if column in data.columns]
        missing = [column for column in expected if column not in data.columns]
        if missing:
            warnings.warn(
                f"Expected column(s) not found and will be skipped: {missing}",
                stacklevel=2,
            )
        if not available:
            raise ValueError(
                f"None of the expected text columns {expected} were found in the file. "
                f"Available columns: {list(data.columns)}"
            )
    return data


def save_to_excel(df: pd.DataFrame, output_filepath: Union[str, Path]) -> Path:
    """Write ``df`` to ``.xlsx``. Requires the optional ``openpyxl`` package."""
    path = Path(output_filepath)
    try:
        df.to_excel(path, index=False, engine="openpyxl")
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise ImportError(
            "Writing .xlsx requires the optional 'openpyxl' package. "
            "Install it with `pip install openpyxl`, or write CSV instead."
        ) from exc
    return path


def process_wos_to_excel(
    wos_filepath: Union[str, Path],
    output_filepath: Union[str, Path],
    **parse_kwargs: object,
) -> Path:
    """Parse a tagged WoS file and write the result to Excel."""
    return save_to_excel(parse_wos_file(wos_filepath, **parse_kwargs), output_filepath)

