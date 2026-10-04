"""Community detection, labelling and structural summarisation.

Consolidates the community code that had drifted across three places:

* ``co_occurrence_network_analysis.ipynb`` - ``calculate_modularity_and_communities``,
  ``label_communities``, ``find_cliques``, ``detect_core_periphery``,
  ``calculate_betweenness_centrality``, ``detect_communities_greedy``
* ``Giant to macro analysis.ipynb``        - inline ``community_louvain`` calls
* ``Networks/Networks.py``                 - ``compute_clusterings``

Louvain is available from NetworkX itself (``networkx >= 2.8``), so the optional
``python-louvain`` package is no longer required. When it *is* installed it is
used by default, because that is what the notebooks originally used.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import networkx as nx

__all__ = [
    "LOUVAIN_BACKENDS",
    "louvain_partition",
    "partition_modularity",
    "detect_communities",
    "label_communities",
    "find_cliques",
    "detect_core_periphery",
    "betweenness_centrality",
    "top_bridges",
    "betweenness_pivots",
    "BETWEENNESS_PIVOT_BUDGET",
    "BETWEENNESS_MIN_PIVOTS",
    "BETWEENNESS_MAX_PIVOTS",
    "BETWEENNESS_EXACT_NODES",
]

#: Selectable Louvain implementations for :func:`louvain_partition`.
LOUVAIN_BACKENDS = ("auto", "networkx", "python-louvain")

#: Graphs at or below this many nodes get an *exact* betweenness pass (``k=None``).
BETWEENNESS_EXACT_NODES = 500

#: Upper and lower bounds on the number of pivot sources used to approximate
#: betweenness on larger graphs.
BETWEENNESS_MAX_PIVOTS = 200
BETWEENNESS_MIN_PIVOTS = 20

#: Edge visits (``pivots * edges``) allowed in one approximate betweenness call.
#: The exact computation is ``O(V * E)`` and sampling ``k`` sources makes it
#: ``O(k * E)``, so on a 65 000-node co-authorship network the full 200 pivots
#: spend well over a minute in a single call. Capping ``k * E`` here keeps a real
#: export responsive while leaving small graphs (and the demo) untouched, because
#: the budget only starts to bite above roughly 25 000 edges.
BETWEENNESS_PIVOT_BUDGET = 5_000_000


def _partition_to_communities(partition: Dict[Any, int]) -> List[Set[Any]]:
    """Group a ``{node: community_id}`` mapping into a list of node sets."""
    grouped: Dict[int, Set[Any]] = {}
    for node, community_id in partition.items():
        grouped.setdefault(community_id, set()).add(node)
    return list(grouped.values())


def _communities_to_partition(communities: Iterable[Set[Any]]) -> Dict[Any, int]:
    """Assign stable ids to communities, largest first (ties broken by node order)."""
    ordered = sorted(communities, key=lambda nodes: (-len(nodes), sorted(map(str, nodes))))
    partition: Dict[Any, int] = {}
    for community_id, nodes in enumerate(ordered):
        for node in nodes:
            partition[node] = community_id
    return partition


def louvain_partition(
    graph: nx.Graph,
    *,
    backend: str = "auto",
    weight: str = "weight",
    seed: Optional[int] = 42,
    resolution: float = 1.0,
) -> Dict[Any, int]:
    """Detect Louvain communities, returning a ``{node: community_id}`` mapping.

    ``backend`` selects the implementation:

    * ``"auto"``           - ``python-louvain`` if importable, else NetworkX
    * ``"python-louvain"`` - force ``community.community_louvain.best_partition``
    * ``"networkx"``       - force ``networkx.community.louvain_communities``

    Both implementations are seeded for reproducibility. An empty graph yields
    an empty mapping; a graph with no edges yields one singleton community per
    node (this matches NetworkX; ``python-louvain`` returns singletons too).
    """
    if backend not in LOUVAIN_BACKENDS:
        raise ValueError(f"backend must be one of {LOUVAIN_BACKENDS}, got {backend!r}")
    if graph.number_of_nodes() == 0:
        return {}

    if backend in ("auto", "python-louvain"):
        try:
            import community.community_louvain as community_louvain
        except ImportError:
            if backend == "python-louvain":
                raise ImportError(
                    "backend='python-louvain' requires the optional 'python-louvain' "
                    "package (`pip install python-louvain`). Use backend='networkx' "
                    "for the built-in implementation."
                ) from None
        else:
            partition = community_louvain.best_partition(
                graph,
                weight=weight,
                resolution=resolution,
                random_state=seed,
            )
            return dict(partition)

    communities = nx.community.louvain_communities(
        graph,
        weight=weight,
        resolution=resolution,
        seed=seed,
    )
    return _communities_to_partition(communities)


def partition_modularity(
    graph: nx.Graph,
    partition: Dict[Any, int],
    *,
    weight: str = "weight",
) -> float:
    """Modularity of a ``{node: community_id}`` partition.

    Returns ``nan`` when there is nothing to measure (empty graph, or every node
    in its own community with no edges).
    """
    communities = _partition_to_communities(partition)
    if not communities:
        return float("nan")
    try:
        return float(nx.community.modularity(graph, communities, weight=weight))
    except ZeroDivisionError:
        return float("nan")


def detect_communities(
    graph: nx.Graph,
    *,
    weight: str = "weight",
    seed: Optional[int] = 42,
    max_nodes: Optional[int] = None,
    max_edges: Optional[int] = None,
) -> Dict[str, Dict[str, Any]]:
    """Run several community-detection algorithms and describe the results.

    Returns ``{algorithm: info}`` where ``info`` holds ``count``, ``sizes``
    (descending), ``modularity``, ``largest`` and ``smallest`` - or ``{"error": msg}``
    if that algorithm failed. This is the shape consumed by
    :func:`techintel.metrics.summarize_graph`.

    Set ``max_nodes`` / ``max_edges`` to skip clustering entirely on graphs that
    are too large; an empty dict is returned in that case. An empty graph also
    returns an empty dict.

    Algorithms: ``louvain``, ``greedy_modularity``, ``asyn_label_propagation``
    and ``label_propagation``.
    """
    if graph.number_of_nodes() == 0:
        return {}
    if graph.number_of_edges() == 0:
        # Nothing to partition, and NetworkX's modularity raises ZeroDivisionError
        # on an edgeless graph. The notebooks guard this the same way.
        return {}
    if max_nodes is not None and graph.number_of_nodes() > max_nodes:
        return {}
    if max_edges is not None and graph.number_of_edges() > max_edges:
        return {}

    algorithms = {
        "louvain": lambda G: _partition_to_communities(louvain_partition(G, weight=weight, seed=seed)),
        "greedy_modularity": lambda G: list(
            nx.community.greedy_modularity_communities(G, weight=weight)
        ),
        "asyn_label_propagation": lambda G: list(nx.community.asyn_lpa_communities(G, seed=seed)),
        "label_propagation": lambda G: list(nx.community.label_propagation_communities(G)),
    }

    results: Dict[str, Dict[str, Any]] = {}
    for name, algorithm in algorithms.items():
        try:
            communities = algorithm(graph)
            if not communities:
                continue
            sizes = sorted((len(community) for community in communities), reverse=True)
            results[name] = {
                "count": len(communities),
                "sizes": sizes,
                "modularity": float(nx.community.modularity(graph, communities, weight=weight)),
                "largest": sizes[0],
                "smallest": sizes[-1],
            }
        except Exception as exc:  # noqa: BLE001 - surfaced to the caller, not swallowed
            results[name] = {"error": str(exc)}
    return results


def label_communities(graph: nx.Graph, partition: Dict[Any, int]) -> nx.Graph:
    """Attach a ``community`` node attribute from a ``{node: community_id}`` mapping."""
    for node, community_id in partition.items():
        if node in graph:
            graph.nodes[node]["community"] = int(community_id)
    return graph


def find_cliques(graph: nx.Graph) -> List[List[Any]]:
    """All maximal cliques in the graph."""
    return list(nx.find_cliques(graph))


def detect_core_periphery(graph: nx.Graph, *, k: Optional[int] = None) -> Tuple[Any, List[Any]]:
    """Split nodes into the ``k``-core and the remaining periphery.

    With ``k=None`` (the notebooks' behaviour) NetworkX returns the graph
    unfiltered, so the core is every node and the periphery is empty. Pass an
    explicit ``k`` for a meaningful split.
    """
    core_nodes = nx.k_core(graph, k=k).nodes()
    core_set = set(core_nodes)
    periphery_nodes = [node for node in graph.nodes() if node not in core_set]
    return core_set, periphery_nodes


def betweenness_pivots(graph: nx.Graph) -> Optional[int]:
    """Pivot count for approximate betweenness, or ``None`` to compute it exactly.

    Graphs up to :data:`BETWEENNESS_EXACT_NODES` nodes are cheap enough to solve
    exactly. Above that the score is sampled, with the pivot count scaled so that
    ``pivots * edges`` stays within :data:`BETWEENNESS_PIVOT_BUDGET` - otherwise a
    single centrality call on a real co-authorship export runs for minutes.
    """
    node_count = graph.number_of_nodes()
    if node_count <= BETWEENNESS_EXACT_NODES:
        return None
    edges = max(graph.number_of_edges(), 1)
    return max(
        BETWEENNESS_MIN_PIVOTS,
        min(BETWEENNESS_MAX_PIVOTS, BETWEENNESS_PIVOT_BUDGET // edges),
    )


def betweenness_centrality(
    graph: nx.Graph,
    *,
    normalized: bool = True,
    k: Optional[int] = None,
    seed: Optional[int] = 42,
    weight: Optional[str] = None,
) -> Dict[Any, float]:
    """Betweenness centrality, with optional pivoting for large graphs."""
    return dict(
        nx.betweenness_centrality(
            graph,
            k=k,
            normalized=normalized,
            weight=weight,
            seed=seed,
        )
    )


def top_bridges(graph: nx.Graph, *, n: int = 5, **centrality_kwargs: Any) -> List[Tuple[Any, float]]:
    """The ``n`` nodes with the highest betweenness centrality."""
    scores = betweenness_centrality(graph, **centrality_kwargs)
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)[:n]


