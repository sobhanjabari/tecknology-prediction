"""Tests for techintel.metrics: counts, indices, degree stats and summaries."""

from __future__ import annotations

import math
import unittest
from unittest import mock

import networkx as nx

from techintel import communities, metrics

from .fixtures import path_graph, two_triangles


class BasicCountsTests(unittest.TestCase):
    def test_triangle(self):
        self.assertEqual(metrics.basic_counts(nx.complete_graph(3)),
                         {"v": 3, "e": 3, "p": 1, "u": 1})

    def test_path_is_acyclic(self):
        self.assertEqual(metrics.basic_counts(path_graph())["u"], 0)

    def test_two_disjoint_triangles(self):
        self.assertEqual(metrics.basic_counts(two_triangles()),
                         {"v": 6, "e": 6, "p": 2, "u": 2})

    def test_empty_graph(self):
        self.assertEqual(metrics.basic_counts(nx.Graph()),
                         {"v": 0, "e": 0, "p": 0, "u": 0})

    def test_isolated_nodes_are_their_own_components(self):
        graph = nx.Graph()
        graph.add_node("a")
        graph.add_edge("b", "c")
        self.assertEqual(metrics.basic_counts(graph), {"v": 3, "e": 1, "p": 2, "u": 0})


class AlphaBetaGammaTests(unittest.TestCase):
    def test_complete_graph_of_three(self):
        self.assertEqual(metrics.alpha_beta_gamma(3, 3, 1),
                         {"alpha": 1.0, "beta": 1.0, "gamma": 1.0})

    def test_complete_graph_of_four(self):
        self.assertEqual(metrics.alpha_beta_gamma(4, 6, 3),
                         {"alpha": 1.0, "beta": 1.5, "gamma": 1.0})

    def test_path_of_four(self):
        self.assertEqual(metrics.alpha_beta_gamma(4, 3, 0),
                         {"alpha": 0.0, "beta": 0.75, "gamma": 0.5})

    def test_empty_graph_is_all_nan(self):
        values = metrics.alpha_beta_gamma(0, 0, 0)
        for key in ("alpha", "beta", "gamma"):
            self.assertTrue(math.isnan(values[key]), key)

    def test_single_node_has_only_beta(self):
        values = metrics.alpha_beta_gamma(1, 0, 0)
        self.assertTrue(math.isnan(values["alpha"]))
        self.assertEqual(values["beta"], 0.0)
        self.assertTrue(math.isnan(values["gamma"]))

    def test_two_nodes_alpha_is_nan_not_zero(self):
        # The network_topology notebook returned 0.0 here; v>=3 is the stricter
        # and defensible rule.
        values = metrics.alpha_beta_gamma(2, 1, 0)
        self.assertTrue(math.isnan(values["alpha"]))
        self.assertEqual(values["beta"], 0.5)
        self.assertEqual(values["gamma"], 1.0)


class DegreeStatsTests(unittest.TestCase):
    def test_regular_graph_has_zero_heterogeneity(self):
        stats = metrics.degree_stats(nx.complete_graph(3))
        self.assertEqual(stats["avg_degree"], 2.0)
        self.assertEqual(stats["heterogeneity"], 0.0)
        self.assertEqual(sorted(stats["degrees"]), [2, 2, 2])

    def test_path_degrees(self):
        stats = metrics.degree_stats(path_graph())
        self.assertEqual(stats["avg_degree"], 1.5)
        self.assertEqual(sorted(stats["degrees"]), [1, 1, 2, 2])

    def test_empty_graph(self):
        stats = metrics.degree_stats(nx.Graph())
        self.assertTrue(math.isnan(stats["avg_degree"]))
        self.assertTrue(math.isnan(stats["heterogeneity"]))
        self.assertEqual(stats["degrees"], [])


class ShortestPathTests(unittest.TestCase):
    def test_complete_graph_of_three(self):
        self.assertEqual(metrics.average_shortest_path_length_safe(nx.complete_graph(3)), 1.0)

    def test_path_of_four(self):
        self.assertAlmostEqual(metrics.average_shortest_path_length_safe(path_graph()),
                               10 / 6, places=10)

    def test_single_node_component_is_skipped(self):
        # A lone node has no shortest paths, so it must not contribute a 0.0.
        graph = nx.Graph()
        graph.add_node("lonely")
        graph.add_edge("a", "b")
        self.assertEqual(metrics.average_shortest_path_length_safe(graph), 1.0)

    def test_disconnected_components_are_averaged(self):
        self.assertEqual(metrics.average_shortest_path_length_safe(two_triangles()), 1.0)

    def test_no_eligible_components_is_nan(self):
        graph = nx.Graph()
        graph.add_node("lonely")
        self.assertTrue(math.isnan(metrics.average_shortest_path_length_safe(graph)))

    def test_empty_graph_is_nan(self):
        self.assertTrue(math.isnan(metrics.average_shortest_path_length_safe(nx.Graph())))


class TriangleAndIsolateTests(unittest.TestCase):
    def test_triangle_count(self):
        self.assertEqual(metrics.triangle_count(nx.complete_graph(3)), 1)

    def test_complete_graph_of_four_has_four_triangles(self):
        self.assertEqual(metrics.triangle_count(nx.complete_graph(4)), 4)

    def test_acyclic_graph_has_none(self):
        self.assertEqual(metrics.triangle_count(path_graph()), 0)

    def test_two_disjoint_triangles(self):
        self.assertEqual(metrics.triangle_count(two_triangles()), 2)

    def test_empty_graph(self):
        self.assertEqual(metrics.triangle_count(nx.Graph()), 0)

    def test_isolated_node_count(self):
        graph = nx.Graph()
        graph.add_nodes_from(["a", "b", "c"])
        graph.add_edge("b", "c")
        self.assertEqual(metrics.isolated_node_count(graph), 1)

    def test_no_isolates(self):
        self.assertEqual(metrics.isolated_node_count(nx.complete_graph(3)), 0)


class AssortativityTests(unittest.TestCase):
    def test_regular_graph_is_undefined(self):
        self.assertTrue(math.isnan(metrics.safe_assortativity(nx.complete_graph(3))))

    def test_path_is_negative(self):
        self.assertAlmostEqual(metrics.safe_assortativity(path_graph()), -0.5, places=10)

    def test_empty_graph_does_not_raise(self):
        self.assertTrue(math.isnan(metrics.safe_assortativity(nx.Graph())))


class TransitivityAndClusteringTests(unittest.TestCase):
    def test_complete_graph_is_fully_clustered(self):
        self.assertEqual(metrics.global_transitivity(nx.complete_graph(3)), 1.0)
        self.assertEqual(metrics.average_clustering(nx.complete_graph(3)), 1.0)

    def test_path_has_no_triangles(self):
        self.assertEqual(metrics.global_transitivity(path_graph()), 0.0)
        self.assertEqual(metrics.average_clustering(path_graph()), 0.0)

    def test_transitivity_and_clustering_are_distinct_measures(self):
        # A triangle with a tail: global transitivity is 0.6 while the mean
        # local clustering coefficient is ~0.583, so these must not be conflated
        # (the original notebook labelled average_clustering as "transitivity").
        graph = nx.Graph()
        graph.add_edges_from([("a", "b"), ("b", "c"), ("c", "a"), ("c", "d")])
        self.assertAlmostEqual(metrics.global_transitivity(graph), 0.6, places=10)
        self.assertAlmostEqual(metrics.average_clustering(graph), 0.5833333333333333, places=10)
        self.assertNotEqual(metrics.global_transitivity(graph),
                            metrics.average_clustering(graph))

    def test_empty_graph_is_nan_not_an_exception(self):
        self.assertTrue(math.isnan(metrics.global_transitivity(nx.Graph())))
        self.assertTrue(math.isnan(metrics.average_clustering(nx.Graph())))


class MacroAnalysisTests(unittest.TestCase):
    def test_keys_are_stable(self):
        self.assertEqual(
            set(metrics.macro_analysis(nx.complete_graph(3))),
            {
                "Nodes", "Edges", "Sub-graphs", "Cycles", "Alpha", "Beta", "Gamma",
                "Avg Degree", "Heterogeneity", "Triangles", "Transitivity",
                "Avg Clustering", "Isolated Nodes", "Assortativity",
            },
        )

    def test_values_on_a_triangle(self):
        result = metrics.macro_analysis(nx.complete_graph(3))
        self.assertEqual(result["Nodes"], 3)
        self.assertEqual(result["Edges"], 3)
        self.assertEqual(result["Cycles"], 1)
        self.assertEqual(result["Triangles"], 1)
        self.assertEqual(result["Avg Degree"], 2.0)

    def test_empty_graph_returns_nan_not_none(self):
        result = metrics.macro_analysis(nx.Graph())
        self.assertTrue(math.isnan(result["Alpha"]))
        self.assertTrue(math.isnan(result["Avg Degree"]))
        self.assertEqual(result["Nodes"], 0)


class SummarizeGraphTests(unittest.TestCase):
    def test_reports_the_headline_numbers(self):
        text = metrics.summarize_graph(nx.complete_graph(3))
        self.assertIn("Nodes: 3", text)
        self.assertIn("Edges: 3", text)
        self.assertIn("Connected components: 1", text)
        self.assertIn("Largest component size: 3", text)

    def test_uses_the_description_attribute(self):
        graph = nx.complete_graph(3)
        graph.graph["description"] = "IPC (4-digit)"
        self.assertIn("Network: IPC (4-digit)", metrics.summarize_graph(graph))

    def test_lists_top_nodes_by_degree(self):
        text = metrics.summarize_graph(path_graph())
        self.assertIn("Top 5 nodes by degree:", text)
        self.assertIn(": 2", text)

    def test_empty_graph_does_not_raise(self):
        # Regression: the Networks.py original called nx.transitivity unguarded,
        # which raises on an empty graph.
        text = metrics.summarize_graph(nx.Graph())
        self.assertIn("Nodes: 0", text)
        self.assertIn("Transitivity: 0.000000", text)
        self.assertIn("Top 5 nodes by degree: (none)", text)

    def test_reports_clustering_when_supplied(self):
        graph = two_triangles()
        info = communities.detect_communities(graph)
        text = metrics.summarize_graph(graph, info)
        self.assertIn("Clustering results:", text)
        self.assertIn("communities, modularity=", text)

    def test_notes_when_clustering_is_skipped(self):
        self.assertIn("Clustering results: (skipped or none)",
                      metrics.summarize_graph(nx.complete_graph(3)))

    def test_renders_clustering_errors_inline(self):
        text = metrics.summarize_graph(
            nx.complete_graph(3), {"broken": {"error": "boom"}}
        )
        self.assertIn("broken: ERROR: boom", text)

    def test_small_components_get_an_exact_average_shortest_path(self):
        # A 4-node path: 3 edges, mean distance (1+2+3+1+2+1)/6 = 10/6.
        text = metrics.summarize_graph(path_graph())
        self.assertIn("Average shortest path (largest CC, unweighted): 1.666667", text)

    def test_a_component_above_the_cap_skips_the_shortest_path_sweep(self):
        # Regression: nx.average_shortest_path_length runs a BFS from every node,
        # so on a real export it is ~3e10 operations and the run never finishes.
        graph = nx.path_graph(metrics.AVERAGE_PATH_EXACT_NODES + 1)
        with mock.patch.object(metrics.nx, "average_shortest_path_length") as patched:
            text = metrics.summarize_graph(graph)
        patched.assert_not_called()
        self.assertIn("Average shortest path (largest CC, unweighted): skipped", text)
        self.assertIn(str(metrics.AVERAGE_PATH_EXACT_NODES), text)

    def test_a_component_at_the_cap_is_still_exact(self):
        graph = nx.path_graph(metrics.AVERAGE_PATH_EXACT_NODES)
        with mock.patch.object(
            metrics.nx, "average_shortest_path_length", return_value=1.0
        ) as patched:
            text = metrics.summarize_graph(graph)
        patched.assert_called_once()
        self.assertIn("Average shortest path (largest CC, unweighted): 1.000000", text)


if __name__ == "__main__":
    unittest.main()

