"""Synthetic Web of Science data for demos, CI and the pipeline's ``--demo`` mode.

No network access and no checked-in data files: records are generated from a
seeded RNG, so every run on every machine produces byte-identical output.

Records are drawn from a small number of *themes*, and each theme has its own
author pool, keyword set, subject categories and institutions. That is deliberate
- it means the co-occurrence graphs built from this data have genuine community
structure instead of collapsing into a single blob, so community detection and
modularity are actually exercised.

One theme is deliberately *separate*: :data:`DEMO_OUTLIER_THEME` shares no
authors, keywords, categories or institutions with the others. It therefore forms
a small detached cluster in every network, so the giant-component step has
something real to remove. Without it the whole graph would already be connected
and that step would be a silent no-op.

The generated text is real WoS tagged format, continuation lines included, so it
round-trips through :func:`techintel.wos.parse_wos_file` rather than bypassing it.
"""

from __future__ import annotations

import random
import tempfile
from pathlib import Path
from typing import Dict, List, Tuple, Union

import pandas as pd

from . import wos

__all__ = [
    "DEMO_THEMES",
    "DEMO_AUTHOR_POOLS",
    "DEMO_BRIDGE_AUTHORS",
    "DEMO_INSTITUTIONS",
    "DEMO_THEME_INSTITUTIONS",
    "DEMO_JOURNALS",
    "DEMO_SHARED_CATEGORIES",
    "DEMO_OUTLIER_THEME",
    "DEMO_OUTLIER_RATE",
    "DEMO_CROSS_THEME_INSTITUTION_RATE",
    "DEMO_FIRST_YEAR",
    "DEMO_YEAR_SPAN",
    "demo_records",
    "demo_tagged_text",
    "write_demo_wos",
    "demo_dataframe",
]

#: ``(theme, keywords, subject_categories)``. A record draws most of its
#: keywords from exactly one theme, which is what creates community structure.
#: The last entry is :data:`DEMO_OUTLIER_THEME` and shares nothing with the rest.
DEMO_THEMES: Tuple[Tuple[str, Tuple[str, ...], Tuple[str, ...]], ...] = (
    (
        "materials",
        ("perovskite", "thin film", "crystallography", "band gap", "annealing"),
        ("Materials Science", "Physics"),
    ),
    (
        "machine learning",
        ("neural network", "transfer learning", "benchmark", "embedding", "classifier"),
        ("Computer Science", "Information Science"),
    ),
    (
        "biomedical",
        ("biomarker", "clinical trial", "cohort study", "genomics", "drug delivery"),
        ("Medicine", "Biology"),
    ),
    (
        "quantum sensing",
        ("quantum sensing", "spin qubit", "optomechanics", "cryostat"),
        ("Optics", "Acoustics"),
    ),
)

#: Index of the deliberately separate theme, and how often it is drawn. Both the
#: keyword set and the categories above are unique to it, so it never merges back
#: into the main cluster.
DEMO_OUTLIER_THEME = len(DEMO_THEMES) - 1
DEMO_OUTLIER_RATE = 0.06

#: One author pool per entry in :data:`DEMO_THEMES`, so co-authorship clusters.
DEMO_AUTHOR_POOLS: Tuple[Tuple[str, ...], ...] = (
    ("Piwowar, Heather", "Priem, Jason", "Novak, Petr", "Tanaka, Kenji", "Meyer, Jonas"),
    ("Nguyen, Minh", "Sharma, Priya", "Kim, Jisoo", "Andersson, Erik", "Ferrari, Giulia"),
    ("Okafor, Chidi", "Silva, Ana", "Yilmaz, Elif", "Petrov, Ivan", "Costa, Bruno"),
    ("Nakamura, Yuki", "Botha, Thandiwe", "Rossi, Marco"),
)

#: Authors that show up in any main theme, which links the clusters together.
DEMO_BRIDGE_AUTHORS: Tuple[str, ...] = ("Dubois, Claire", "Haddad, Layla")

#: ``(institution, "City, Region PostalCode Country")`` - the address tail that
#: :func:`techintel.wos.extract_country_from_address` has to parse. Three of the
#: entries exercise the USA / Turkey normalisation.
DEMO_INSTITUTIONS: Tuple[Tuple[str, str], ...] = (
    ("Univ Washington", "Seattle, WA 98195 USA"),
    ("OpenAlex", "Vancouver, BC, Canada"),
    ("Bogazici Univ", "Istanbul, Turkey"),
    ("Tech Univ Munich", "Munich, Germany"),
    ("Tsinghua Univ", "Beijing, China"),
    ("Univ Tokyo", "Tokyo, Japan"),
    # Two extra institutions share a country with an earlier entry, so the
    # organisation graph (C3) is genuinely different from the country graph
    # instead of being a relabelling of it.
    ("MIT", "Cambridge, MA 02139 USA"),
    ("Univ Toronto", "Toronto, ON, Canada"),
    ("Univ Cape Town", "Cape Town, South Africa"),
    ("Nanyang Tech Univ", "Singapore, Singapore"),
)

#: Institutions each theme draws from, index-aligned with :data:`DEMO_THEMES`.
#: Tying institutions to themes is what gives the country and organisation graphs
#: real community structure; drawing from the whole pool at random made both
#: graphs complete, with density 1.0 and modularity 0.
DEMO_THEME_INSTITUTIONS: Tuple[Tuple[str, ...], ...] = (
    ("Tech Univ Munich", "Bogazici Univ"),
    ("MIT", "Univ Toronto", "OpenAlex"),
    ("Tsinghua Univ", "Univ Washington"),
    ("Univ Cape Town", "Nanyang Tech Univ"),
)

#: How often a main-theme record borrows one institution from another main theme.
#: This keeps the main themes in a single connected cluster rather than three
#: islands. The separate theme never borrows, and is never borrowed from.
DEMO_CROSS_THEME_INSTITUTION_RATE = 0.15

#: Institution name -> address tail, for turning a sampled name into a C1 line.
_ADDRESS_BY_INSTITUTION: Dict[str, str] = dict(DEMO_INSTITUTIONS)

#: Categories that may accompany any theme. Real WoS records routinely carry
#: cross-disciplinary categories, and without them the subject-category graph
#: would be three disjoint pairs with no giant component worth analysing.
DEMO_SHARED_CATEGORIES: Tuple[str, ...] = ("Multidisciplinary Sciences", "Engineering")

#: Journal strings include a comma, which the parser trims to the first segment.
DEMO_JOURNALS: Tuple[str, ...] = (
    "PeerJ, Inc",
    "Nature",
    "Scientometrics",
    "Research Policy",
    "IEEE Access",
)

#: First year and the number of years spanned by generated records.
DEMO_FIRST_YEAR = 2015
DEMO_YEAR_SPAN = 8


def demo_records(n: int = 60, *, seed: int = 0) -> List[Dict[str, object]]:
    """Generate ``n`` synthetic records as dicts of already-split field values.

    Keys: ``authors``, ``title``, ``journal``, ``year``, ``doi``, ``keywords``,
    ``addresses`` (``(author, institution, address_tail)`` triples),
    ``organisations``, ``categories``, ``funding`` and ``citations``.
    """
    if n < 0:
        raise ValueError(f"n must be non-negative, got {n}")

    rng = random.Random(seed)
    theme_count = len(DEMO_THEMES)
    main_themes = tuple(i for i in range(theme_count) if i != DEMO_OUTLIER_THEME)
    records: List[Dict[str, object]] = []

    for position in range(n):
        # A small fraction of records join the separate theme. It shares no
        # authors, keywords, categories or institutions with the rest, so it
        # shows up as a detached cluster in every network.
        if rng.random() < DEMO_OUTLIER_RATE:
            theme = DEMO_OUTLIER_THEME
        else:
            theme = rng.choice(main_themes)
        is_separate = theme == DEMO_OUTLIER_THEME

        _, theme_keywords, theme_categories = DEMO_THEMES[theme]

        keywords = rng.sample(theme_keywords, k=rng.randint(3, len(theme_keywords)))
        # A few records borrow a keyword from the next theme, which gives the
        # keyword graph a handful of inter-community edges. The separate theme
        # neither borrows nor is borrowed from, so it stays isolated.
        if not is_separate and rng.random() < 0.25:
            keywords.append(rng.choice(DEMO_THEMES[(theme + 1) % len(main_themes)][1]))

        author_pool = DEMO_AUTHOR_POOLS[theme]
        authors = rng.sample(author_pool, k=rng.randint(2, min(4, len(author_pool))))
        if not is_separate and rng.random() < 0.3:
            authors.append(rng.choice(DEMO_BRIDGE_AUTHORS))

        theme_institutions = DEMO_THEME_INSTITUTIONS[theme]
        institutions = list(
            rng.sample(theme_institutions, k=rng.randint(1, len(theme_institutions)))
        )
        # Occasionally borrow an institution from another main theme, so the main
        # themes form one connected cluster instead of three islands.
        if not is_separate and rng.random() < DEMO_CROSS_THEME_INSTITUTION_RATE:
            donor = rng.choice([t for t in main_themes if t != theme])
            institutions.append(rng.choice(DEMO_THEME_INSTITUTIONS[donor]))

        addresses = []
        for offset, author in enumerate(authors):
            institution = institutions[offset % len(institutions)]
            surname, given = author.split(", ", 1)
            initials = " ".join(part[0] for part in given.split())
            addresses.append(
                (f"{surname} {initials}", institution, _ADDRESS_BY_INSTITUTION[institution])
            )

        records.append(
            {
                "authors": authors,
                "title": f"{keywords[0].title()} for {keywords[-1].title()}",
                "journal": rng.choice(DEMO_JOURNALS),
                "year": DEMO_FIRST_YEAR + rng.randrange(DEMO_YEAR_SPAN),
                "doi": f"10.1000/demo.{seed}.{position + 1}",
                "keywords": keywords,
                "addresses": addresses,
                "organisations": institutions,
                "categories": rng.sample(
                    theme_categories, k=rng.randint(1, len(theme_categories))
                )
                # The separate theme never takes a shared category either, so the
                # subject-category graph detaches just like the other four.
                + (
                    [rng.choice(DEMO_SHARED_CATEGORIES)]
                    if not is_separate and rng.random() < 0.35
                    else []
                ),
                "funding": rng.choice(
                    ("National Science Foundation", "European Commission", "JSPS KAKENHI")
                ),
                "citations": rng.randint(0, 120),
            }
        )
    return records


def demo_tagged_text(n: int = 60, *, seed: int = 0) -> str:
    """Render :func:`demo_records` as a WoS tagged export (``.txt``)."""
    lines: List[str] = ["FN Clarivate", "VR 1.0"]

    for record in demo_records(n, seed=seed):
        authors = list(record["authors"])  # type: ignore[arg-type]
        keywords = list(record["keywords"])  # type: ignore[arg-type]
        addresses = list(record["addresses"])  # type: ignore[arg-type]
        organisations = list(record["organisations"])  # type: ignore[arg-type]
        categories = list(record["categories"])  # type: ignore[arg-type]

        lines.append("PT J")
        lines.append(f"AF {authors[0]}")
        for author in authors[1:]:  # continuation lines are indented
            lines.append(f"   {author}")
        lines.append(f"TI {record['title']}")
        lines.append(f"SO {record['journal']}")
        lines.append(f"PY {record['year']}")
        lines.append(f"DI {record['doi']}")
        lines.append(f"DE {keywords[0]};")
        for keyword in keywords[1:]:
            lines.append(f"   {keyword};")
        first_label, first_institution, first_tail = addresses[0]
        lines.append(f"C1 [{first_label}] {first_institution}, {first_tail}")
        for label, institution, tail in addresses[1:]:
            lines.append(f"   [{label}] {institution}, {tail}")
        # The bare "; ;" leaves an empty item, which clean_c3_field drops.
        lines.append("C3 " + "; ; ".join(organisations))
        lines.append(f"FU {record['funding']}")
        lines.append(f"TC {record['citations']}")
        lines.append(f"WC {'; '.join(categories)}")
        lines.append("ER")
        lines.append("")

    lines.append("EF")
    return "\n".join(lines) + "\n"


def write_demo_wos(path: Union[str, Path], n: int = 60, *, seed: int = 0) -> Path:
    """Write a tagged demo export to ``path`` and return it."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(demo_tagged_text(n, seed=seed), encoding="utf-8")
    return target


def demo_dataframe(n: int = 60, *, seed: int = 0) -> pd.DataFrame:
    """Return the demo records as a parsed DataFrame.

    The tagged text is round-tripped through the real parser (via a temporary
    file) rather than assembled by hand, so the DataFrame can never drift out of
    sync with :func:`demo_tagged_text`.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / "demo_wos.txt"
        write_demo_wos(path, n, seed=seed)
        return wos.parse_wos_file(path)
