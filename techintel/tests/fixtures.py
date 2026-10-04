"""Shared fixtures: a literal WoS tagged export and small deterministic graphs."""

from __future__ import annotations

from typing import Dict

import networkx as nx

# Two complete WoS records. Record 1 exercises every continuation rule:
# `AF` (joined with "|", commas preserved), `DE` (joined with a space), `C1`
# (joined with "|"), `C3` and `WC`. Record 2 is a single-author Turkish record
# used for the country-normalisation assertions.
WOS_TAGGED_TEXT = """FN Clarivate
VR 1.0
PT J
AF Piwowar, Heather
   Priem, Jason R
TI Open access matters
   a follow-up study
SO PeerJ, Inc
PY 2018
DI 10.7717/peerj.4375
DE open access; bibliometrics;
   citation analysis
C1 [Piwowar H] Univ Washington, Seattle, WA 98195 USA
   [Priem J] OpenAlex, Vancouver, BC, Canada
C3 Univ Washington; ; OpenAlex
FU National Science Foundation
TC 10
WC Decision Sciences; Information Science
ER

PT J
AF Someone, Else
TI Second record
SO Nature
PY 2021
C1 [Someone E] Bogazici Univ, Istanbul, Turkey
TC 3
ER

EF
"""

#: A tagged file with no `PT` record at all.
WOS_TAGGED_EMPTY = """FN Clarivate
VR 1.0
EF
"""

#: Expected values for record 1 of :data:`WOS_TAGGED_TEXT`.
WOS_RECORD_1: Dict[str, str] = {
    "Authors": "Piwowar, Heather|Priem, Jason R",
    "Title": "Open access matters a follow-up study",
    "Journal": "PeerJ",
    "Year": "2018",
    "DOI": "10.7717/peerj.4375",
    "Keywords": "open access; bibliometrics; citation analysis",
    "Addresses": (
        "[Piwowar H] Univ Washington, Seattle, WA 98195 USA|"
        "[Priem J] OpenAlex, Vancouver, BC, Canada"
    ),
    "C3": "Univ Washington; OpenAlex",
    "Funding": "National Science Foundation",
    "Cite": "10",
    "Subject_Category": "Decision Sciences; Information Science",
    "Countries": "USA ; Canada",
}


#: A raw Web of Science "Full Record" CSV export: long human-readable headers,
#: ``;``-delimited authors, and address blocks separated by ``"; "`` rather than
#: ``"|"``. Two records, two institutions, two countries.
WOS_RAW_EXPORT_CSV = """\
Publication Type,Authors,Author Full Names,Article Title,Source Title,Publication Year,Author Keywords,WoS Categories,Affiliations,Addresses,DOI,Abstract,Funding Orgs
J,"Piwowar, H; Priem, J","Piwowar, Heather; Priem, Jason",Open access matters,PeerJ,2018,"open access; bibliometrics","Information Science; Decision Sciences",University of Washington,"[Piwowar, Heather] Univ Washington, Seattle, WA 98195 USA; [Priem, Jason] OpenAlex, Vancouver, BC, Canada",10.7717/peerj.4375,An abstract.,National Science Foundation
J,"Silva, A","Silva, Ana",Citation analysis,PeerJ,2019,"citation analysis; bibliometrics","Information Science",Universidade de Lisboa,"[Silva, Ana] Univ Lisbon, Lisbon, Portugal",10.7717/peerj.9999,Another abstract.,Fundacao para a Ciencia
"""


def two_triangles() -> nx.Graph:
    """Two disjoint triangles: `a-b-c` and `x-y-z`, every edge weight 1."""
    graph = nx.Graph()
    graph.add_edges_from(
        [("a", "b"), ("b", "c"), ("c", "a"), ("x", "y"), ("y", "z"), ("z", "x")]
    )
    return graph


def path_graph() -> nx.Graph:
    """A 4-node path `a-b-c-d`; `b` and `c` are the natural bridges."""
    return nx.path_graph(["a", "b", "c", "d"])


def weighted_triangle() -> nx.Graph:
    """A single weighted triangle."""
    graph = nx.Graph()
    graph.add_edge("a", "b", weight=1.0)
    graph.add_edge("b", "c", weight=2.0)
    graph.add_edge("c", "a", weight=3.0)
    return graph
