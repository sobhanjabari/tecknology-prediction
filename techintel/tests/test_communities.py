"""Tests for techintel.communities: Louvain, modularity, labelling, bridges."""

from __future__ import annotations

import math
import unittest

import networkx as nx

from techintel import communities

from .fixtures import path_graph, two_triangles


def _has_python_louvain() -> bool:
    try:
        import community.community_louvain  # noqa: F401
    except ImportError:
        return False
    return True


class LouvainPartitionTests(unittest.TestCase):
    def test_two_triangles_split_into_two_communities(self):
        partition = communities.louvain_partition(two_triangles(), backend="networkx")
        self.assertEqual(len(set(partition.values())), 2)
        self.assertEqual(partition["a"], partition["b"])
        self.assertEqual(partition["a"], partition["c"])
        self.assertEqual(partition["x"], partition["y"])
        self.assertNotEqual(partition["a"], partition["x"])

    def test_empty_graph_yields_empty_mapping(self):
        self.assertEqual(communities.louvain_partition(nx.Graph()), {})

    def test_edgeless_graph_yields_singletons(self):
        graph = nx.Graph()
        graph.add_nodes_from(["a", "b", "c"])
        partition = communities.louvain_partition(graph, backend="networkx")
        self.assertEqual(len(set(partition.values())), 3)

    def test_single_node(self):
        graph = nx.Graph()
        graph.add_node("only")
        self.assertEqual(communities.louvain_partition(graph, backend="networkx"), {"only": 0})

    def test_seeded_result_is_reproducible(self):
        first = communities.louvain_partition(two_triangles(), backend="networkx", seed=7)
        second = communities.louvain_partition(two_triangles(), backend="networkx", seed=7)
        self.assertEqual(first, second)

    def test_unknown_backend_raises(self):
        with self.assertRaises(ValueError) as ctx:
            communities.louvain_partition(two_triangles(), backend="spectral")
        self.assertIn("backend must be one of", str(ctx.exception))

    @unittest.skipIf(_has_python_louvain(), "python-louvain is installed")
    def test_forcing_python_louvain_without_it_raises_actionably(self):
        with self.assertRaises(ImportError) as ctx:
            communities.louvain_partition(two_triangles(), backend="python-louvain")
        self.assertIn("python-louvain", str(ctx.exception))

    @unittest.skipUnless(_has_python_louvain(), "python-louvain is not installed")
    def test_python_louvain_backend_also_finds_two_communities(self):
        partition = communities.louvain_partition(two_triangles(), backend="python-louvain")
        self.assertEqual(len(set(partition.values())), 2)

    def test_auto_backend_never_raises_without_optional_dep(self):
        partition = communities.louvain_partition(two_triangles(), backend="auto")
        self.assertEqual(len(set(partition.values())), 2)


class ModularityTests(unittest.TestCase):
    def test_two_triangles_modularity(self):
        graph = two_triangles()
        partition = communities.louvain_partition(graph, backend="networkx")
        self.assertAlmostEqual(communities.partition_modularity(graph, partition), 0.5, places=10)

    def test_single_community_has_zero_modularity(self):
        graph = two_triangles()
        self.assertAlmostEqual(
            communities.partition_modularity(graph, {node: 0 for node in graph}), 0.0, places=10
        )

    def test_empty_partition_is_nan(self):
        self.assertTrue(math.isnan(communities.partition_modularity(nx.Graph(), {})))


class DetectCommunitiesTests(unittest.TestCase):
    def test_all_algorithms_report_the_two_triangles(self):
        info = communities.detect_communities(two_triangles())
        self.assertEqual(
            set(info),
            {"louvain", "greedy_modularity", "asyn_label_propagation", "label_propagation"},
        )
        for name, data in info.items():
            with self.subTest(algorithm=name):
                self.assertNotIn("error", data)
                self.assertEqual(data["count"], 2)
                self.assertEqual(data["sizes"], [3, 3])
                self.assertEqual(data["largest"], 3)
                self.assertEqual(data["smallest"], 3)
                self.assertAlmostEqual(data["modularity"], 0.5, places=10)

    def test_empty_graph_returns_empty_dict(self):
        self.assertEqual(communities.detect_communities(nx.Graph()), {})

    def test_edgeless_graph_returns_empty_dict(self):
        # NetworkX modularity raises ZeroDivisionError here, so it must be guarded.
        graph = nx.Graph()
        graph.add_nodes_from(["a", "b", "c"])
        self.assertEqual(communities.detect_communities(graph), {})

    def test_max_nodes_guard_skips_clustering(self):
        self.assertEqual(communities.detect_communities(two_triangles(), max_nodes=5), {})

    def test_max_edges_guard_skips_clustering(self):
        self.assertEqual(communities.detect_communities(two_triangles(), max_edges=5), {})

    def test_guards_do_not_trigger_when_generous(self):
        info = communities.detect_communities(two_triangles(), max_nodes=100, max_edges=100)
        self.assertEqual(len(info), 4)


class LabelCommunitiesTests(unittest.TestCase):
    def test_sets_the_community_attribute(self):
        graph = two_triangles()
        partition = communities.louvain_partition(graph, backend="networkx")
        communities.label_communities(graph, partition)
        for node, community_id in partition.items():
            self.assertEqual(graph.nodes[node]["community"], community_id)

    def test_returns_the_same_graph_object(self):
        graph = two_triangles()
        self.assertIs(communities.label_communities(graph, {"a": 0}), graph)

    def test_unknown_nodes_are_ignored(self):
        graph = two_triangles()
        communities.label_communities(graph, {"ghost": 0, "a": 1})
        self.assertEqual(graph.nodes["a"]["community"], 1)
        self.assertNotIn("ghost", graph)


class CliqueAndCoreTests(unittest.TestCase):
    def test_find_cliques_on_a_triangle(self):
        triangle = nx.Graph()
        triangle.add_edges_from([("a", "b"), ("b", "c"), ("c", "a")])
        cliques = communities.find_cliques(triangle)
        self.assertEqual(len(cliques), 1)
        self.assertEqual(sorted(cliques[0]), ["a", "b", "c"])

    def test_find_cliques_on_a_path_are_edges(self):
        cliques = communities.find_cliques(path_graph())
        self.assertEqual(len(cliques), 3)
        self.assertTrue(all(len(clique) == 2 for clique in cliques))

    def test_k_core_default_keeps_everything(self):
        graph = two_triangles()
        core, periphery = communities.detect_core_periphery(graph)
        self.assertEqual(len(core), 6)
        self.assertEqual(periphery, [])

    def test_k_core_two_drops_the_pendant(self):
        graph = nx.Graph()
        graph.add_edges_from([("a", "b"), ("b", "c"), ("c", "a"), ("c", "d")])
        core, periphery = communities.detect_core_periphery(graph, k=2)
        self.assertEqual(sorted(core), ["a", "b", "c"])
        self.assertEqual(periphery, ["d"])

    def test_one_core_on_a_path_keeps_everything(self):
        core, periphery = communities.detect_core_periphery(path_graph(), k=1)
        self.assertEqual(len(core), 4)
        self.assertEqual(periphery, [])


class BetweennessTests(unittest.TestCase):
    def test_path_middle_nodes_bridge(self):
        scores = communities.betweenness_centrality(path_graph())
        self.assertEqual(scores["a"], 0.0)
        self.assertEqual(scores["d"], 0.0)
        self.assertAlmostEqual(scores["b"], 2 / 3, places=10)
        self.assertAlmostEqual(scores["c"], 2 / 3, places=10)

    def test_top_bridges_ranks_highest_first(self):
        top = communities.top_bridges(path_graph(), n=2)
        self.assertEqual({node for node, _ in top}, {"b", "c"})
        for _, value in top:
            self.assertAlmostEqual(value, 2 / 3, places=10)

    def test_top_bridges_respects_n(self):
        self.assertEqual(len(communities.top_bridges(path_graph(), n=3)), 3)

    def test_complete_graph_has_no_bridges(self):
        scores = communities.betweenness_centrality(nx.complete_graph(3))
        for value in scores.values():
            self.assertEqual(value, 0.0)


class _StubGraph:
    """Just enough of the ``nx.Graph`` interface for :func:`betweenness_pivots`."""

    def __init__(self, nodes: int, edges: int) -> None:
        self._nodes = nodes
        self._edges = edges

    def number_of_nodes(self) -> int:
        return self._nodes

    def number_of_edges(self) -> int:
        return self._edges


class BetweennessPivotTests(unittest.TestCase):
    """The pivot count has to keep a real export from taking minutes."""

    def _graph(self, nodes: int, edges_per_node: int) -> nx.Graph:
        graph = nx.Graph()
        graph.add_nodes_from(range(nodes))
        for node in range(nodes):
            for step in range(1, edges_per_node + 1):
                graph.add_edge(node, (node + step) % nodes)
        return graph

    def test_small_graphs_are_solved_exactly(self):
        graph = self._graph(200, 3)
        self.assertLessEqual(graph.number_of_nodes(), communities.BETWEENNESS_EXACT_NODES)
        self.assertIsNone(communities.betweenness_pivots(graph))

    def test_a_graph_at_the_boundary_is_still_exact(self):
        self.assertIsNone(
            communities.betweenness_pivots(
                _StubGraph(communities.BETWEENNESS_EXACT_NODES, 10_000)
            )
        )

    def test_a_graph_inside_the_budget_gets_the_maximum_pivots(self):
        # ~25 000 edges is where the budget starts to bite; well below it nothing
        # is given up, so the demo and small exports keep the full sample.
        graph = self._graph(600, 40)
        self.assertLess(graph.number_of_edges(), 25_000)
        self.assertEqual(
            communities.betweenness_pivots(graph), communities.BETWEENNESS_MAX_PIVOTS
        )

    def test_a_large_graph_is_capped_by_the_budget(self):
        graph = self._graph(4000, 20)
        pivots = communities.betweenness_pivots(graph)
        self.assertIsNotNone(pivots)
        self.assertLessEqual(pivots, communities.BETWEENNESS_MAX_PIVOTS)
        self.assertLessEqual(
            pivots * graph.number_of_edges(), communities.BETWEENNESS_PIVOT_BUDGET
        )

    def test_the_pivot_count_never_drops_below_the_floor(self):
        # A graph dense enough that the budget alone would divide the pivot count
        # down to single digits still gets a usable sample.
        self.assertEqual(
            communities.betweenness_pivots(_StubGraph(1000, 1_000_000)),
            communities.BETWEENNESS_MIN_PIVOTS,
        )


if __name__ == "__main__":
    unittest.main()
