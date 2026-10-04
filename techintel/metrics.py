"""Network-structure metrics for co-occurrence / citation graphs.

These calculations were triplicated before this module existed:

* ``network_topology_metrics_analysis.ipynb`` - ``basic_counts``, ``alpha_beta_gamma``,
  ``degree_stats``, ``average_shortest_path_length_safe``, ``triangle_count``,
  ``isolated_node_count``, ``safe_assortativity``
* ``Giant to macro analysis.ipynb``          - ``macro_analysis``
* ``Networks/Networks.py``                   - ``summarize_graph``

They disagreed on their edge-case guards; this module uses the strictest of the
three and documents the choice. Metrics assume an **undirected** graph unless
noted; directed input is coerced where the underlying NetworkX routine requires
it.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import networkx as nx

__all__ = [
    "basic_counts",
    "alpha_beta_gamma",
    "degree_stats",
    "average_shortest_path_length_safe",
    "triangle_count",
    "isolated_node_count",
    "safe_assortativity",
    "global_transitivity",
    "average_clustering",
    "macro_analysis",
    "summarize_graph",
    "AVERAGE_PATH_EXACT_NODES",
]

_NAN = float("nan")

#: Largest component for which :func:`summarize_graph` computes an exact average
#: shortest path length.
#:
#: ``nx.average_shortest_path_length`` runs a BFS from *every* node, so it costs
#: ``O(V * (V + E))``. On a real 49 000-record WoS export the co-authorship
#: network's largest component holds ~65 000 nodes and ~430 000 edges - roughly
#: 3e10 operations, i.e. hours, for one line of a text report. Above this bound
#: the figure is reported as skipped rather than hanging the run.
AVERAGE_PATH_EXACT_NODES = 5000


def _undirected(graph: nx.Graph) -> nx.Graph:
    """Return an undirected view/copy of ``graph``."""
    return graph if not graph.is_directed() else graph.to_undirected()


def basic_counts(graph: nx.Graph) -> Dict[str, int]:
    """Return ``v`` (nodes), ``e`` (edges), ``p`` (components) and ``u`` (cycles).

    ``u`` is the cyclomatic number, ``e - v + p``; it is 0 for a forest and is
    only meaningful once ``v > 0``.
    """
    v = graph.number_of_nodes()
    e = graph.number_of_edges()
    p = nx.number_connected_components(_undirected(graph)) if v else 0
    u = e - v + p if v else 0
    return {"v": v, "e": e, "p": p, "u": u}


def alpha_beta_gamma(v: int, e: int, u: int) -> Dict[str, float]:
    """Compute the classic alpha, beta and gamma connectivity indices.

    * ``beta  = e / v``            - defined for ``v >= 1``
    * ``gamma = e / (v(v-1)/2)``   - defined for ``v >= 2``
    * ``alpha = u / (2v - 5)``     - defined for ``v >= 3``

    Undefined combinations yield ``nan`` rather than raising.

    .. note::
       ``Giant to macro analysis.ipynb`` used ``v > 2`` for alpha while
       ``network_topology_metrics_analysis.ipynb`` only guarded ``2v - 5 != 0``
       (letting ``v = 2`` return a meaningless ``0.0``). The stricter ``v >= 3``
       rule is used here.
    """
    alpha = u / (2 * v - 5) if v >= 3 else _NAN
    beta = e / v if v >= 1 else _NAN
    gamma = e / (v * (v - 1) / 2) if v >= 2 else _NAN
    return {"alpha": alpha, "beta": beta, "gamma": gamma}


def degree_stats(graph: nx.Graph) -> Dict[str, Any]:
    """Average degree and heterogeneity (variance of the degree distribution)."""
    v = graph.number_of_nodes()
    if v == 0:
        return {"avg_degree": _NAN, "heterogeneity": _NAN, "degrees": []}
    degrees = [degree for _, degree in graph.degree()]
    avg_degree = sum(degrees) / v
    heterogeneity = sum((degree - avg_degree) ** 2 for degree in degrees) / v
    return {"avg_degree": avg_degree, "heterogeneity": heterogeneity, "degrees": degrees}


def average_shortest_path_length_safe(graph: nx.Graph) -> float:
    """Mean of the per-component average shortest path lengths.

    Components with fewer than two nodes are skipped (undefined for a single
    node). Returns ``nan`` when no component qualifies.
    """
    undirected = _undirected(graph)
    lengths: List[float] = []
    for component_nodes in nx.connected_components(undirected):
        if len(component_nodes) < 2:
            continue
        lengths.append(nx.average_shortest_path_length(undirected.subgraph(component_nodes)))
    if not lengths:
        return _NAN
    return sum(lengths) / len(lengths)


def triangle_count(graph: nx.Graph) -> int:
    """Total number of triangles, each counted once."""
    undirected = _undirected(graph)
    return sum(nx.triangles(undirected).values()) // 3


def isolated_node_count(graph: nx.Graph) -> int:
    """Number of nodes with degree 0."""
    return len(list(nx.isolates(graph)))


def safe_assortativity(graph: nx.Graph) -> float:
    """Degree assortativity, returning ``nan`` where NetworkX cannot compute it.

    A graph whose nodes all share one degree has zero degree variance, which
    makes the coefficient undefined. NumPy signals that case with a
    ``RuntimeWarning``; the warning is suppressed here because returning ``nan``
    is this function's documented contract.
    """
    import warnings

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            return float(nx.degree_assortativity_coefficient(graph))
    except (ZeroDivisionError, ValueError, TypeError):
        return _NAN


def global_transitivity(graph: nx.Graph) -> float:
    """Fraction of triples that are transitive (``nx.transitivity``)."""
    if graph.number_of_nodes() == 0:
        return _NAN
    return float(nx.transitivity(_undirected(graph)))


def average_clustering(graph: nx.Graph) -> float:
    """Mean of the local clustering coefficients (``nx.average_clustering``)."""
    if graph.number_of_nodes() == 0:
        return _NAN
    return float(nx.average_clustering(_undirected(graph)))


def macro_analysis(graph: nx.Graph) -> Dict[str, Any]:
    """One-shot macro summary, using the keys from ``Giant to macro analysis``.

    Undefined quantities come back as ``nan`` rather than ``None`` so the result
    feeds straight into a :class:`pandas.DataFrame`.
    """
    counts = basic_counts(graph)
    indices = alpha_beta_gamma(counts["v"], counts["e"], counts["u"])
    degrees = degree_stats(graph)
    return {
        "Nodes": counts["v"],
        "Edges": counts["e"],
        "Sub-graphs": counts["p"],
        "Cycles": counts["u"],
        "Alpha": indices["alpha"],
        "Beta": indices["beta"],
        "Gamma": indices["gamma"],
        "Avg Degree": degrees["avg_degree"],
        "Heterogeneity": degrees["heterogeneity"],
        "Triangles": triangle_count(graph),
        "Transitivity": global_transitivity(graph),
        "Avg Clustering": average_clustering(graph),
        "Isolated Nodes": isolated_node_count(graph),
        "Assortativity": safe_assortativity(graph),
    }


def summarize_graph(
    graph: nx.Graph,
    cluster_info: Optional[Dict[str, Dict[str, Any]]] = None,
) -> str:
    """Human-readable multi-line summary of a graph's macro structure.

    ``cluster_info`` maps an algorithm name to the dict produced by
    :func:`techintel.communities.detect_communities` (or an ``{"error": ...}``
    entry, which is rendered inline).

    .. note::
       The ``Networks/Networks.py`` original called ``nx.transitivity`` and
       ``nx.average_clustering`` unguarded, which raises on an empty graph. Those
       are now guarded and report ``0.0``.

       The average shortest path is only computed for largest components up to
       :data:`AVERAGE_PATH_EXACT_NODES` nodes. Beyond that the exact BFS sweep is
       skipped and the line says so, because it is ``O(V * (V + E))`` and takes
       hours on a real export.
    """
    lines: List[str] = []
    lines.append(f"Network: {graph.graph.get('description', graph.name)}")
    lines.append(f"Nodes: {graph.number_of_nodes()}")
    lines.append(f"Edges: {graph.number_of_edges()}")

    undirected = _undirected(graph)
    if graph.number_of_nodes():
        lines.append(f"Density: {nx.density(graph):.6f}")
        lines.append(f"Transitivity: {nx.transitivity(undirected):.6f}")
        lines.append(
            f"Average clustering coefficient (weighted): "
            f"{nx.average_clustering(undirected, weight='weight'):.6f}"
        )
    else:
        lines.append("Density: 0.000000")
        lines.append("Transitivity: 0.000000")
        lines.append("Average clustering coefficient (weighted): 0.000000")

    components = list(nx.connected_components(undirected))
    lines.append(f"Connected components: {len(components)}")
    if components:
        largest = max(components, key=len)
        lines.append(f"Largest component size: {len(largest)}")
        subgraph = undirected.subgraph(largest)
        if subgraph.number_of_nodes() > AVERAGE_PATH_EXACT_NODES:
            lines.append(
                "Average shortest path (largest CC, unweighted): skipped "
                f"(component has {subgraph.number_of_nodes()} nodes; exact BFS "
                f"from every node is O(V*(V+E)) and is capped at "
                f"{AVERAGE_PATH_EXACT_NODES})"
            )
        else:
            avg_path = (
                nx.average_shortest_path_length(subgraph)
                if subgraph.number_of_nodes() > 1
                else 0.0
            )
            lines.append(f"Average shortest path (largest CC, unweighted): {avg_path:.6f}")
    else:
        lines.append("Largest component size: 0")
        lines.append("Average shortest path (largest CC): 0.000000")

    degree_items = sorted(graph.degree, key=lambda item: item[1], reverse=True)[:5]
    if degree_items:
        lines.append("Top 5 nodes by degree:")
        for node, degree in degree_items:
            lines.append(f"  {node}: {degree}")
    else:
        lines.append("Top 5 nodes by degree: (none)")

    if cluster_info:
        lines.append("Clustering results:")
        for algorithm, info in cluster_info.items():
            if "error" in info:
                lines.append(f"  {algorithm}: ERROR: {info['error']}")
            else:
                lines.append(
                    f"  {algorithm}: {info['count']} communities, "
                    f"modularity={info['modularity']:.4f}, "
                    f"largest={info['largest']}, smallest={info['smallest']}"
                )
    else:
        lines.append("Clustering results: (skipped or none)")
    return "\n".join(lines)



