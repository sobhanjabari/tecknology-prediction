"""Graph I/O and co-occurrence graph construction.

Consolidates the graph helpers that were copy-pasted between
``co_occurrence_network_analysis.ipynb``, ``Giant to macro analysis.ipynb``,
``network_topology_metrics_analysis.ipynb`` and ``Networks/Networks.py``.

Three subtly different ``load_graph`` implementations existed:

* ``co_occurrence``      - optional self-loop stripping
* ``Giant to macro``     - forced ``to_undirected()`` **and** self-loop stripping
* ``network_topology``   - an existence check with a friendly message

:func:`load_graph` covers all three via keyword arguments.
"""

from __future__ import annotations

import re
from itertools import combinations
from pathlib import Path
from typing import Any, Callable, List, Optional, Union

import networkx as nx

__all__ = [
    "SPLIT_PEOPLE_ORG",
    "SPLIT_CODES",
    "split_field",
    "sanitize_node",
    "load_graph",
    "save_graph",
    "get_giant_component",
    "extract_and_save_giant_component",
    "build_cooccurrence_graph",
]

#: Splitter for people / organisation fields - deliberately does **not** split on
#: a bare comma, so ``"Piwowar, Heather"`` survives intact.
SPLIT_PEOPLE_ORG = re.compile(r"\s*;;\s*|\s*;\s*")

#: Splitter for classification codes (IPC/CPC/WoS categories), where commas are
#: also separators.
SPLIT_CODES = re.compile(r"\s*;;\s*|\s*;\s*|\s*,\s*")


def split_field(value: object, *, kind: str) -> List[str]:
    """Split a multi-valued field into stripped, non-empty entries.

    ``kind`` is ``"people"`` or ``"codes"`` (see :data:`SPLIT_PEOPLE_ORG` and
    :data:`SPLIT_CODES`). Non-string and blank input yields ``[]``.
    """
    if kind not in ("people", "codes"):
        raise ValueError(f"kind must be 'people' or 'codes', got {kind!r}")
    if not isinstance(value, str) or not value.strip():
        return []
    pattern = SPLIT_CODES if kind == "codes" else SPLIT_PEOPLE_ORG
    return [part.strip() for part in pattern.split(value) if part.strip()]


def sanitize_node(value: object) -> str:
    """Collapse whitespace/newlines in a node label so GraphML stays valid."""
    if value is None:
        return ""
    text = re.sub(r"[\r\n\t]+", " ", str(value))
    return re.sub(r"\s+", " ", text).strip()


def load_graph(
    path: Union[str, Path],
    *,
    remove_self_loops: bool = False,
    undirected: bool = False,
    require_exists: bool = True,
) -> nx.Graph:
    """Read a GraphML file, optionally normalising it.

    ``remove_self_loops`` drops self-loop edges; ``undirected`` coerces the
    result with ``to_undirected()``. When ``require_exists`` is true a missing
    file raises ``FileNotFoundError`` with a pointer to the path argument rather
    than a raw NetworkX/OS error.
    """
    graph_path = Path(path)
    if require_exists and not graph_path.exists():
        raise FileNotFoundError(
            f"GraphML file not found at '{graph_path}'.\n"
            f"Update the path variable to point at your graph file."
        )

    graph = nx.read_graphml(graph_path)
    if undirected:
        graph = graph.to_undirected()
    if remove_self_loops:
        graph.remove_edges_from(nx.selfloop_edges(graph))
    return graph


def save_graph(graph: nx.Graph, path: Union[str, Path]) -> Path:
    """Write ``graph`` as GraphML, creating the parent directory if needed."""
    graph_path = Path(path)
    graph_path.parent.mkdir(parents=True, exist_ok=True)
    nx.write_graphml(graph, graph_path, encoding="utf-8")
    return graph_path


def get_giant_component(graph: nx.Graph) -> nx.Graph:
    """Largest (weakly) connected component, as a copy."""
    if graph.number_of_nodes() == 0:
        return graph.copy()
    if graph.is_directed():
        largest = max(nx.weakly_connected_components(graph), key=len)
    else:
        largest = max(nx.connected_components(graph), key=len)
    return graph.subgraph(largest).copy()


def extract_and_save_giant_component(
    graphml_file: Union[str, Path],
    output_file: Union[str, Path],
    **load_kwargs: Any,
) -> nx.Graph:
    """Load a graph, keep its giant component, save it and report the sizes."""
    graph = load_graph(graphml_file, **load_kwargs)
    giant = get_giant_component(graph)
    save_graph(giant, output_file)
    return giant


def build_cooccurrence_graph(
    df: Any,
    column: str,
    *,
    separator: str = ";",
    strip_items: bool = True,
    dedupe: bool = True,
    split_kind: Optional[str] = None,
    transform: Optional[Callable[[str], str]] = None,
    sanitize: bool = False,
    name: Optional[str] = None,
    description: Optional[str] = None,
) -> nx.Graph:
    """Build a weighted undirected co-occurrence graph from a text column.

    Every pair of items appearing together in one row receives an edge, or has
    its ``weight`` incremented. The column is absent from ``df`` -> an empty
    graph is returned.

    Parameters
    ----------
    separator:
        Delimiter used to split the column, ignored when ``split_kind`` is set.
    split_kind:
        ``"people"`` or ``"codes"`` - use :func:`split_field` instead of a plain
        separator, so that ``"Piwowar, Heather"`` is not split on its comma.
    dedupe:
        Drop duplicate items **within** a row before pairing. This is the
        behaviour of ``Networks/Networks.py``.

        .. warning::
           The ``co_occurrence_network_analysis.ipynb`` original left this off.
           With duplicates present it created self-loops (``"a; a"`` -> edge
           ``a-a``) and double-counted every pair, inflating ``weight``. The
           default here is ``True``; pass ``False`` only to reproduce old counts.
    transform:
        Optional per-item normaliser (e.g. a code-prefix reducer), applied
        before de-duplication.
    sanitize:
        Apply :func:`sanitize_node` to every item.
    name:
        Value for ``graph.name``.
    description:
        Stored as ``graph.graph["description"]``, which
        :func:`techintel.metrics.summarize_graph` reports as the network name.
    """
    graph = nx.Graph(name=name if name is not None else column)
    if description is not None:
        graph.graph["description"] = description
    if column not in df.columns:
        return graph

    for value in df[column]:
        if not isinstance(value, str):
            continue

        if split_kind is not None:
            members = split_field(value, kind=split_kind)
        else:
            members = value.split(separator)
            if strip_items:
                members = [item.strip() for item in members]
            members = [item for item in members if item]

        if transform is not None:
            members = [transform(member) for member in members]
        if sanitize:
            members = [sanitize_node(member) for member in members]
        members = [member for member in members if member]
        if not members:
            continue

        unique_members = sorted(set(members)) if dedupe else members
        for member in unique_members:
            graph.add_node(member)

        for source, target in combinations(unique_members, 2):
            if graph.has_edge(source, target):
                graph[source][target]["weight"] += 1
            else:
                graph.add_edge(source, target, weight=1)

    return graph


