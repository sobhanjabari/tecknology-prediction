"""Tests for techintel.graphs: field splitting, GraphML I/O, co-occurrence building."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import networkx as nx
import pandas as pd

from techintel import graphs


class SplitFieldTests(unittest.TestCase):
    def test_people_does_not_split_on_comma(self):
        # The whole point of the people splitter: "Piwowar, Heather" is one name.
        self.assertEqual(
            graphs.split_field("Piwowar, Heather; Priem, Jason R", kind="people"),
            ["Piwowar, Heather", "Priem, Jason R"],
        )

    def test_codes_splits_on_comma(self):
        self.assertEqual(
            graphs.split_field("A61K, C07D; G06N", kind="codes"),
            ["A61K", "C07D", "G06N"],
        )

    def test_double_semicolon_is_a_separator(self):
        self.assertEqual(graphs.split_field("a;;b", kind="codes"), ["a", "b"])

    def test_whitespace_is_stripped_and_blanks_dropped(self):
        self.assertEqual(graphs.split_field("  a ; ; b  ", kind="codes"), ["a", "b"])

    def test_non_string_and_blank_yield_empty_list(self):
        for value in (None, 123, "", "   ", float("nan")):
            with self.subTest(value=value):
                self.assertEqual(graphs.split_field(value, kind="people"), [])

    def test_unknown_kind_raises(self):
        with self.assertRaises(ValueError):
            graphs.split_field("a;b", kind="nonsense")


class SanitizeNodeTests(unittest.TestCase):
    def test_collapses_newlines_and_whitespace(self):
        self.assertEqual(graphs.sanitize_node("a\nb\tc   d"), "a b c d")

    def test_strips_edges(self):
        self.assertEqual(graphs.sanitize_node("  padded  "), "padded")

    def test_none_becomes_empty(self):
        self.assertEqual(graphs.sanitize_node(None), "")

    def test_non_string_is_coerced(self):
        self.assertEqual(graphs.sanitize_node(42), "42")


class GraphIoTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_round_trip(self):
        original = nx.Graph()
        original.add_edge("a", "b", weight=3)
        path = graphs.save_graph(original, self.tmp / "nested" / "g.graphml")
        self.assertTrue(path.exists())
        self.assertEqual(nx.number_of_edges(graphs.load_graph(path)), 1)

    def test_save_creates_parent_directory(self):
        graphs.save_graph(nx.Graph(), self.tmp / "deep" / "deeper" / "g.graphml")
        self.assertTrue((self.tmp / "deep" / "deeper" / "g.graphml").exists())

    def test_missing_file_raises_with_guidance(self):
        with self.assertRaises(FileNotFoundError) as ctx:
            graphs.load_graph(self.tmp / "absent.graphml")
        self.assertIn("GraphML file not found", str(ctx.exception))

    def test_require_exists_false_lets_networkx_raise(self):
        with self.assertRaises(Exception):
            graphs.load_graph(self.tmp / "absent.graphml", require_exists=False)

    def test_remove_self_loops(self):
        graph = nx.Graph()
        graph.add_edge("a", "a")
        graph.add_edge("a", "b")
        path = graphs.save_graph(graph, self.tmp / "loops.graphml")
        loaded = graphs.load_graph(path, remove_self_loops=True)
        self.assertEqual(list(loaded.edges()), [("a", "b")])

    def test_self_loops_kept_by_default(self):
        graph = nx.Graph()
        graph.add_edge("a", "a")
        path = graphs.save_graph(graph, self.tmp / "loops.graphml")
        self.assertEqual(nx.number_of_selfloops(graphs.load_graph(path)), 1)

    def test_undirected_coercion(self):
        directed = nx.DiGraph()
        directed.add_edge("a", "b")
        path = graphs.save_graph(directed, self.tmp / "d.graphml")
        self.assertTrue(graphs.load_graph(path, undirected=True).is_directed() is False)


class GiantComponentTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_picks_the_largest_component(self):
        graph = nx.Graph()
        graph.add_edges_from([("a", "b"), ("b", "c"), ("x", "y")])
        giant = graphs.get_giant_component(graph)
        self.assertEqual(sorted(giant.nodes()), ["a", "b", "c"])

    def test_empty_graph_is_returned_unchanged(self):
        self.assertEqual(graphs.get_giant_component(nx.Graph()).number_of_nodes(), 0)

    def test_directed_graph_uses_weak_connectivity(self):
        graph = nx.DiGraph()
        graph.add_edges_from([("a", "b"), ("b", "c"), ("x", "y")])
        self.assertEqual(sorted(graphs.get_giant_component(graph).nodes()), ["a", "b", "c"])

    def test_extract_and_save_writes_the_file(self):
        graph = nx.Graph()
        graph.add_edges_from([("a", "b"), ("b", "c"), ("x", "y")])
        source = graphs.save_graph(graph, self.tmp / "full.graphml")
        target = self.tmp / "giant.graphml"
        giant = graphs.extract_and_save_giant_component(source, target)
        self.assertTrue(target.exists())
        self.assertEqual(sorted(giant.nodes()), ["a", "b", "c"])


class BuildCooccurrenceTests(unittest.TestCase):
    def test_weights_accumulate_across_rows(self):
        df = pd.DataFrame({"kw": ["a; b", "a; b", "a; b"]})
        graph = graphs.build_cooccurrence_graph(df, "kw")
        self.assertEqual(graph["a"]["b"]["weight"], 3)

    def test_dedupe_avoids_self_loops_and_inflated_weights(self):
        # Regression: "a; b; a" used to yield a self-loop `a-a` AND weight 2 on
        # `a-b`, because the duplicate was paired with everything twice.
        df = pd.DataFrame({"kw": ["a; b; a"]})
        graph = graphs.build_cooccurrence_graph(df, "kw")
        self.assertEqual(sorted(graph.edges()), [("a", "b")])
        self.assertEqual(graph["a"]["b"]["weight"], 1)
        self.assertEqual(nx.number_of_selfloops(graph), 0)

    def test_dedupe_false_reproduces_the_old_behaviour(self):
        df = pd.DataFrame({"kw": ["a; b; a"]})
        graph = graphs.build_cooccurrence_graph(df, "kw", dedupe=False)
        self.assertEqual(graph["a"]["b"]["weight"], 2)
        self.assertEqual(nx.number_of_selfloops(graph), 1)

    def test_missing_column_yields_empty_graph(self):
        graph = graphs.build_cooccurrence_graph(pd.DataFrame({"other": ["a; b"]}), "kw")
        self.assertEqual(graph.number_of_nodes(), 0)
        self.assertEqual(graph.number_of_edges(), 0)

    def test_non_string_values_are_skipped(self):
        df = pd.DataFrame({"kw": [None, float("nan"), 42, "a; b"]})
        graph = graphs.build_cooccurrence_graph(df, "kw")
        self.assertEqual(sorted(graph.nodes()), ["a", "b"])

    def test_blank_and_separator_only_rows_are_skipped(self):
        df = pd.DataFrame({"kw": ["", "   ", ";", ";;", "a"]})
        graph = graphs.build_cooccurrence_graph(df, "kw")
        self.assertEqual(graph.number_of_edges(), 0)
        self.assertEqual(sorted(graph.nodes()), ["a"])

    def test_strip_items_false_keeps_padding(self):
        df = pd.DataFrame({"kw": [" a ; b "]})
        graph = graphs.build_cooccurrence_graph(df, "kw", strip_items=False)
        self.assertEqual(sorted(graph.nodes()), [" a ", " b "])

    def test_split_kind_codes_splits_commas(self):
        df = pd.DataFrame({"ipc": ["A61K, C07D; G06N"]})
        graph = graphs.build_cooccurrence_graph(df, "ipc", split_kind="codes")
        self.assertEqual(sorted(graph.nodes()), ["A61K", "C07D", "G06N"])
        self.assertEqual(graph.number_of_edges(), 3)

    def test_split_kind_people_preserves_commas(self):
        df = pd.DataFrame({"authors": ["Piwowar, Heather; Priem, Jason R"]})
        graph = graphs.build_cooccurrence_graph(df, "authors", split_kind="people")
        self.assertEqual(sorted(graph.nodes()), ["Piwowar, Heather", "Priem, Jason R"])

    def test_transform_is_applied_before_dedupe(self):
        # Two different full codes collapse to one prefix, so they must dedupe.
        df = pd.DataFrame({"ipc": ["A61K31/00; A61K45/00"]})
        graph = graphs.build_cooccurrence_graph(
            df, "ipc", split_kind="codes", transform=lambda code: code.split("/")[0][:4]
        )
        self.assertEqual(sorted(graph.nodes()), ["A61K"])
        self.assertEqual(graph.number_of_edges(), 0)

    def test_sanitize_collapses_newlines(self):
        df = pd.DataFrame({"kw": ["alpha\nbeta; gamma"]})
        graph = graphs.build_cooccurrence_graph(df, "kw", sanitize=True)
        self.assertIn("alpha beta", graph.nodes())

    def test_graph_name_defaults_to_column(self):
        graph = graphs.build_cooccurrence_graph(pd.DataFrame({"kw": ["a; b"]}), "kw")
        self.assertEqual(graph.name, "kw")

    def test_graph_name_can_be_overridden(self):
        graph = graphs.build_cooccurrence_graph(
            pd.DataFrame({"kw": ["a; b"]}), "kw", name="keyword co-occurrence"
        )
        self.assertEqual(graph.name, "keyword co-occurrence")

    def test_custom_separator(self):
        df = pd.DataFrame({"kw": ["a|b"]})
        graph = graphs.build_cooccurrence_graph(df, "kw", separator="|")
        self.assertEqual(sorted(graph.edges()), [("a", "b")])


if __name__ == "__main__":
    unittest.main()
