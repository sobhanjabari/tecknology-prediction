"""Tests for techintel.demo, techintel.pipeline and techintel.verify.

Everything here is offline and file-system-local: the pipeline is exercised on
seeded synthetic data written to a temporary directory, never on a real export.
"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import networkx as nx
import pandas as pd

from techintel import demo, pipeline, verify

from .fixtures import WOS_TAGGED_EMPTY, WOS_TAGGED_TEXT, path_graph, two_triangles

#: Small enough to stay fast, large enough to give every network some edges.
DEMO_N = 60


class _TempDirTestCase(unittest.TestCase):
    """Base class giving each test a scratch directory that is cleaned up."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)


class DemoDataTests(_TempDirTestCase):
    def test_records_are_deterministic_for_a_seed(self):
        self.assertEqual(demo.demo_records(20, seed=3), demo.demo_records(20, seed=3))

    def test_different_seeds_differ(self):
        self.assertNotEqual(demo.demo_records(20, seed=1), demo.demo_records(20, seed=2))

    def test_negative_count_is_rejected(self):
        with self.assertRaises(ValueError):
            demo.demo_records(-1)

    def test_zero_records_is_allowed(self):
        self.assertEqual(demo.demo_records(0), [])

    def test_record_shape(self):
        record = demo.demo_records(1, seed=0)[0]
        self.assertEqual(
            set(record),
            {
                "authors", "title", "journal", "year", "doi", "keywords",
                "addresses", "organisations", "categories", "funding", "citations",
            },
        )
        self.assertGreaterEqual(len(record["authors"]), 2)
        self.assertTrue(record["title"])

    def test_tagged_text_round_trips_through_the_real_parser(self):
        path = demo.write_demo_wos(self.tmp / "demo.txt", 40, seed=0)
        parsed = pipeline.load_records(path)
        self.assertEqual(len(parsed), 40)
        self.assertEqual(list(parsed.columns), list(pipeline.wos.WOS_COLUMNS) + ["Countries"])

    def test_dataframe_matches_the_tagged_text(self):
        from_text = pipeline.load_records(demo.write_demo_wos(self.tmp / "d.txt", 30, seed=5))
        from_helper = demo.demo_dataframe(30, seed=5)
        pd.testing.assert_frame_equal(from_text, from_helper)

    def test_continuation_lines_are_exercised(self):
        # Multi-author records must survive the parser's `|` joining rule.
        parsed = demo.demo_dataframe(40, seed=0)
        multi = parsed[parsed["Authors"].str.contains(r"\|", regex=True)]
        self.assertFalse(multi.empty)
        self.assertTrue((multi["Authors"].str.count(r"\|") >= 1).all())

    def test_country_normalisation_is_exercised(self):
        # The demo data must include the two values that the fixed country
        # extractor is responsible for normalising.
        countries = demo.demo_dataframe(DEMO_N, seed=0)["Countries"]
        seen = {part.strip() for value in countries for part in value.split(";") if part.strip()}
        self.assertIn("USA", seen)
        self.assertIn("Turkiye", seen)
        self.assertIn("Canada", seen)

    def test_empty_c3_items_are_dropped(self):
        # The generator emits the "A; ; B" shape, which clean_c3_field collapses.
        c3_values = demo.demo_dataframe(40, seed=0)["C3"]
        self.assertFalse(any(";;" in value for value in c3_values))

    def test_categories_appear_on_one_wc_line(self):
        # WoS emits `WC a; b` on a single line; the parser joins continuation
        # lines with a space, so a wrapped WC would merge into one item.
        parsed = demo.demo_dataframe(40, seed=0)
        for value in parsed["Subject_Category"]:
            self.assertNotIn("  ", value)

    def test_every_theme_has_an_institution_pool(self):
        # DEMO_THEME_INSTITUTIONS is index-aligned with DEMO_THEMES; a mismatch
        # would mis-assign institutions and flatten the country graph again.
        self.assertEqual(len(demo.DEMO_THEME_INSTITUTIONS), len(demo.DEMO_THEMES))
        known = dict(demo.DEMO_INSTITUTIONS)
        for pool in demo.DEMO_THEME_INSTITUTIONS:
            self.assertTrue(pool)
            for name in pool:
                self.assertIn(name, known)

    def test_the_separate_theme_shares_nothing_with_the_others(self):
        # This is what makes the giant-component step do real work. If the
        # separate theme ever shares a value, every network silently reconnects
        # and the step becomes a no-op again.
        separate = demo.DEMO_OUTLIER_THEME
        others = [i for i in range(len(demo.DEMO_THEMES)) if i != separate]

        _, keywords, categories = demo.DEMO_THEMES[separate]
        other_keywords = {k for i in others for k in demo.DEMO_THEMES[i][1]}
        other_categories = {c for i in others for c in demo.DEMO_THEMES[i][2]}

        self.assertFalse(set(keywords) & other_keywords)
        self.assertFalse(set(categories) & other_categories)
        self.assertFalse(set(categories) & set(demo.DEMO_SHARED_CATEGORIES))
        self.assertFalse(set(demo.DEMO_AUTHOR_POOLS[separate]) & set(demo.DEMO_BRIDGE_AUTHORS))
        self.assertFalse(
            set(demo.DEMO_THEME_INSTITUTIONS[separate])
            & {n for i in others for n in demo.DEMO_THEME_INSTITUTIONS[i]}
        )

    def test_the_separate_theme_is_actually_generated(self):
        # A rate of zero (or a theme never drawn) would leave the tests above
        # passing while the data had no detached cluster at all.
        records = demo.demo_records(200, seed=0)
        separate_authors = set(demo.DEMO_AUTHOR_POOLS[demo.DEMO_OUTLIER_THEME])
        drawn = [
            record for record in records
            if separate_authors & set(record["authors"])  # type: ignore[arg-type]
        ]
        self.assertTrue(drawn)
        for record in drawn:
            # Never picks up a bridge author, and never a shared category.
            self.assertFalse(set(record["authors"]) & set(demo.DEMO_BRIDGE_AUTHORS))  # type: ignore[arg-type]
            self.assertFalse(set(record["categories"]) & set(demo.DEMO_SHARED_CATEGORIES))  # type: ignore[arg-type]



class LoadRecordsTests(_TempDirTestCase):
    def test_tagged_text_is_detected(self):
        self.assertTrue(pipeline._looks_tagged(WOS_TAGGED_TEXT))

    def test_delimited_headers_are_not_mistaken_for_tags(self):
        self.assertFalse(pipeline._looks_tagged("PT,AU,TI\nJ,Smith,Title\n"))
        self.assertFalse(pipeline._looks_tagged("PT\tAU\tTI\nJ\tSmith\tTitle\n"))

    def test_separator_sniffing(self):
        self.assertEqual(pipeline._sniff_separator("a\tb\tc\n"), "\t")
        self.assertEqual(pipeline._sniff_separator("a,b,c\n"), ",")
        self.assertEqual(pipeline._sniff_separator("\n\n"), ",")

    def test_loads_a_tagged_export(self):
        path = self.tmp / "records.txt"
        path.write_text(WOS_TAGGED_TEXT, encoding="utf-8")
        data = pipeline.load_records(path)
        self.assertEqual(len(data), 2)
        self.assertEqual(data.loc[0, "Countries"], "USA ; Canada")

    def test_loads_a_comma_delimited_export(self):
        path = self.tmp / "records.csv"
        path.write_text('Authors,Keywords\n"A; B","x; y"\n', encoding="utf-8")
        data = pipeline.load_records(path)
        self.assertEqual(len(data), 1)
        self.assertIn("Authors", data.columns)

    def test_loads_a_tab_delimited_txt_export(self):
        # Web of Science exports tab-delimited data with a .txt extension.
        path = self.tmp / "records.txt"
        path.write_text('Authors\tKeywords\n"A; B"\t"x; y"\n', encoding="utf-8")
        data = pipeline.load_records(path)
        self.assertEqual(list(data.columns), ["Authors", "Keywords"])
        self.assertEqual(len(data), 1)

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            pipeline.load_records(self.tmp / "absent.txt")

    def test_header_only_csv_raises(self):
        path = self.tmp / "empty.csv"
        path.write_text("Authors,Keywords\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            pipeline.load_records(path)

    def test_tagged_file_with_no_records_raises(self):
        path = self.tmp / "empty.txt"
        path.write_text(WOS_TAGGED_EMPTY, encoding="utf-8")
        with self.assertRaises(ValueError):
            pipeline.load_records(path)


class BuildNetworksTests(unittest.TestCase):
    def setUp(self):
        self.data = demo.demo_dataframe(DEMO_N, seed=0)

    def test_all_default_specs_are_built(self):
        built, skipped = pipeline.build_networks(self.data)
        self.assertEqual(skipped, [])
        self.assertEqual(list(built), [spec.name for spec in pipeline.DEFAULT_NETWORK_SPECS])

    def test_authors_spec_splits_on_pipe_not_semicolon(self):
        # WoS joins authors with `|`; splitting on `;` would fuse each record's
        # authors into a single node and lose the whole co-authorship structure.
        authors_only = [s for s in pipeline.DEFAULT_NETWORK_SPECS if s.name == "authors"]
        built, _ = pipeline.build_networks(self.data, authors_only)
        graph = built["authors"]
        self.assertTrue(all("|" not in node for node in graph))
        self.assertGreater(graph.number_of_nodes(), 10)
        self.assertEqual(len(list(nx.selfloop_edges(graph))), 0)

    def test_every_default_network_has_edges(self):
        built, _ = pipeline.build_networks(self.data)
        for name, graph in built.items():
            with self.subTest(network=name):
                self.assertGreater(graph.number_of_edges(), 0)

    def test_missing_column_is_skipped_not_fatal(self):
        built, skipped = pipeline.build_networks(
            self.data[["Authors"]], pipeline.DEFAULT_NETWORK_SPECS
        )
        self.assertEqual(list(built), ["authors"])
        self.assertEqual(
            skipped, ["keywords", "subject_categories", "countries", "organisations"]
        )

    def test_spec_metadata_reaches_the_graph(self):
        built, _ = pipeline.build_networks(self.data)
        self.assertEqual(built["authors"].name, "authors")
        self.assertEqual(built["authors"].graph["description"], "Author co-authorship")

    def test_dedupe_default_prevents_self_loops(self):
        frame = pd.DataFrame({"K": ["a; a; b"]})
        built, _ = pipeline.build_networks(frame, [pipeline.NetworkSpec("k", "K")])
        graph = built["k"]
        self.assertEqual(sorted(graph.nodes()), ["a", "b"])
        self.assertEqual(graph["a"]["b"]["weight"], 1)


class AnalyzeGraphTests(unittest.TestCase):
    def test_empty_graph_does_not_raise(self):
        analysis = pipeline.analyze_graph(nx.Graph())
        self.assertEqual(analysis["communities"], {})
        self.assertEqual(analysis["top_nodes"], [])
        self.assertEqual(analysis["top_bridges"], [])

    def test_edgeless_graph_skips_clustering(self):
        graph = nx.Graph()
        graph.add_nodes_from(["a", "b"])
        self.assertEqual(pipeline.analyze_graph(graph)["communities"], {})

    def test_metrics_are_always_present(self):
        # macro_analysis returns display-style keys, not the raw basic_counts ones.
        analysis = pipeline.analyze_graph(path_graph())
        for key in ("Nodes", "Edges", "Sub-graphs", "Cycles", "Alpha", "Beta", "Gamma"):
            with self.subTest(key=key):
                self.assertIn(key, analysis["metrics"])

    def test_top_nodes_are_sorted_by_degree(self):
        analysis = pipeline.analyze_graph(path_graph(), top_n=2)
        self.assertEqual(analysis["top_nodes"], [("b", 2), ("c", 2)])

    def test_bridges_are_the_path_middles(self):
        analysis = pipeline.analyze_graph(path_graph(), top_n=2)
        self.assertEqual([node for node, _ in analysis["top_bridges"]], ["b", "c"])

    def test_communities_are_detected_on_a_structured_graph(self):
        analysis = pipeline.analyze_graph(two_triangles())
        self.assertIn("louvain", analysis["communities"])
        self.assertEqual(analysis["communities"]["louvain"]["count"], 2)

    def test_cluster_guard_skips_detection_but_keeps_metrics(self):
        analysis = pipeline.analyze_graph(two_triangles(), cluster_guard=(1, 1))
        self.assertEqual(analysis["communities"], {})
        self.assertEqual(analysis["metrics"]["Nodes"], 6)


class RunPipelineTests(_TempDirTestCase):
    def _run_demo(self, **kwargs) -> pipeline.PipelineResult:
        out_dir = kwargs.pop("out_dir", self.tmp / "out")
        return pipeline.run_pipeline(out_dir=out_dir, demo_count=DEMO_N, seed=0, **kwargs)

    def test_demo_run_is_clean(self):
        result = self._run_demo()
        self.assertEqual(result.exit_code, pipeline.EXIT_OK)
        self.assertEqual(result.records, DEMO_N)
        self.assertEqual(len(result.networks), len(pipeline.DEFAULT_NETWORK_SPECS))

    def test_bundle_files_are_written(self):
        result = self._run_demo()
        self.assertTrue(result.manifest_path.is_file())
        self.assertTrue(result.metrics_path.is_file())
        self.assertTrue(result.summary_path.is_file())

    def test_graphml_is_written_for_every_network(self):
        result = self._run_demo()
        for network in result.networks:
            with self.subTest(network=network.name):
                self.assertTrue(network.graphml_path.is_file())
                self.assertTrue(network.giant_graphml_path.is_file())

    def test_graphml_carries_community_attributes(self):
        result = self._run_demo()
        graph = nx.read_graphml(result.networks[0].graphml_path)
        self.assertTrue(all("community" in attrs for _, attrs in graph.nodes(data=True)))

    def test_giant_component_never_exceeds_the_source(self):
        for network in self._run_demo().networks:
            with self.subTest(network=network.name):
                self.assertLessEqual(
                    network.giant.number_of_nodes(), network.graph.number_of_nodes()
                )
                self.assertLessEqual(
                    network.giant.number_of_edges(), network.graph.number_of_edges()
                )

    def test_no_giant_keeps_the_full_graph(self):
        for network in self._run_demo(giant=False).networks:
            self.assertIsNone(network.giant)
            self.assertIsNone(network.giant_graphml_path)
            self.assertIs(network.target, network.graph)

    def test_giant_component_actually_drops_nodes(self):
        # The demo data deliberately contains a detached cluster, so the giant
        # component must be strictly smaller than the full graph. If this stops
        # holding, the giant-component step has quietly become a no-op.
        for network in self._run_demo().networks:
            with self.subTest(network=network.name):
                self.assertLess(
                    network.giant.number_of_nodes(), network.graph.number_of_nodes()
                )

    def test_countries_and_organisations_have_community_structure(self):
        # Both used to be complete graphs (density 1.0, modularity 0) because
        # institutions were drawn from the whole pool regardless of theme.
        by_name = {network.name: network for network in self._run_demo().networks}
        for name in ("countries", "organisations"):
            with self.subTest(network=name):
                network = by_name[name]
                louvain = network.analysis["communities"]["louvain"]
                self.assertLess(nx.density(network.graph), 1.0)
                self.assertGreater(louvain["count"], 1)
                self.assertGreater(louvain["modularity"], 0.0)

    def test_off_giant_nodes_carry_a_community_label(self):
        # Clustering runs on the giant component, so nodes outside it are absent
        # from the partition. They must still be labelled, or Gephi shows them
        # with no community at all.
        for network in self._run_demo().networks:
            with self.subTest(network=network.name):
                graph = nx.read_graphml(network.graphml_path)
                self.assertTrue(
                    all("community" in attrs for _, attrs in graph.nodes(data=True))
                )
                off_giant = [
                    attrs["community"]
                    for _, attrs in graph.nodes(data=True)
                    if attrs["community"] == pipeline.UNLABELLED_COMMUNITY
                ]
                self.assertTrue(off_giant)
                # The giant copy is fully covered by real communities.
                giant = nx.read_graphml(network.giant_graphml_path)
                self.assertNotIn(
                    pipeline.UNLABELLED_COMMUNITY,
                    [attrs["community"] for _, attrs in giant.nodes(data=True)],
                )

    def test_metrics_csv_has_one_row_per_network(self):
        result = self._run_demo()
        metrics = pd.read_csv(result.metrics_path)
        self.assertEqual(len(metrics), len(result.networks))
        self.assertEqual(list(metrics["network"]), [n.name for n in result.networks])
        self.assertTrue(metrics["modularity"].notna().all())

    def test_summary_mentions_every_network(self):
        text = self._run_demo().summary_path.read_text(encoding="utf-8")
        for network in self._run_demo().networks:
            self.assertIn(f"[{network.name}]", text)

    def test_manifest_is_valid_json_with_relative_paths(self):
        result = self._run_demo()
        manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["tool"], "techintel.pipeline")
        self.assertEqual(manifest["exit_code"], pipeline.EXIT_OK)
        self.assertTrue(manifest["demo"])
        for entry in manifest["networks"]:
            self.assertFalse(Path(entry["graphml"]).is_absolute())
            self.assertTrue((result.out_dir / entry["graphml"]).is_file())
            self.assertTrue((result.out_dir / entry["giant_graphml"]).is_file())

    def test_manifest_has_no_bare_nan_or_infinity(self):
        text = self._run_demo().manifest_path.read_text(encoding="utf-8")
        self.assertNotIn("NaN", text)
        self.assertNotIn("Infinity", text)

    def test_runs_from_a_real_tagged_export(self):
        path = demo.write_demo_wos(self.tmp / "export.txt", DEMO_N, seed=0)
        result = pipeline.run_pipeline(input_path=path, out_dir=self.tmp / "from_file", seed=0)
        self.assertEqual(result.exit_code, pipeline.EXIT_OK)
        self.assertEqual(result.records, DEMO_N)
        self.assertFalse(result.demo)

    def test_input_and_demo_are_mutually_exclusive(self):
        with self.assertRaises(ValueError):
            pipeline.run_pipeline(
                input_path=self.tmp / "x.txt", out_dir=self.tmp / "o", demo_count=5
            )

    def test_neither_input_nor_demo_raises(self):
        with self.assertRaises(ValueError):
            pipeline.run_pipeline(out_dir=self.tmp / "o")

    def test_subset_of_specs_is_honoured(self):
        keywords = [s for s in pipeline.DEFAULT_NETWORK_SPECS if s.name == "keywords"]
        result = self._run_demo(specs=keywords)
        self.assertEqual([n.name for n in result.networks], ["keywords"])

    def test_missing_columns_produce_a_partial_run(self):
        path = self.tmp / "authors_only.csv"
        path.write_text('Authors\n"A; B"\n"B; C"\n', encoding="utf-8")
        result = pipeline.run_pipeline(input_path=path, out_dir=self.tmp / "partial")
        self.assertEqual(result.exit_code, pipeline.EXIT_PARTIAL)
        self.assertEqual(
            result.skipped, ["keywords", "subject_categories", "countries", "organisations"]
        )

    def test_empty_demo_is_partial_not_a_crash(self):
        result = pipeline.run_pipeline(out_dir=self.tmp / "zero", demo_count=0)
        self.assertEqual(result.exit_code, pipeline.EXIT_PARTIAL)
        self.assertEqual(result.usable, [])
        self.assertTrue(result.metrics_path.is_file())

    def test_skip_clustering_still_writes_metrics(self):
        metrics = pd.read_csv(self._run_demo(cluster_guard=(0, 0)).metrics_path)
        self.assertTrue(metrics["community_count"].isna().all())

    def test_runs_are_reproducible(self):
        first = self._run_demo(out_dir=self.tmp / "a")
        second = self._run_demo(out_dir=self.tmp / "b")
        self.assertEqual(
            [n.target.number_of_edges() for n in first.networks],
            [n.target.number_of_edges() for n in second.networks],
        )
        self.assertEqual(
            [n.analysis["communities"]["louvain"]["count"] for n in first.networks],
            [n.analysis["communities"]["louvain"]["count"] for n in second.networks],
        )


class JsonableTests(unittest.TestCase):
    def test_nan_and_infinity_become_none(self):
        self.assertIsNone(pipeline._jsonable(float("nan")))
        self.assertIsNone(pipeline._jsonable(float("inf")))

    def test_nested_containers_are_converted(self):
        converted = pipeline._jsonable({"a": [float("nan"), 1], "b": {"c": 2}})
        self.assertEqual(converted, {"a": [None, 1], "b": {"c": 2}})

    def test_sets_become_lists(self):
        self.assertEqual(pipeline._jsonable({1}), [1])

    def test_relative_to_keeps_paths_inside_the_bundle(self):
        root = Path("out")
        self.assertEqual(
            pipeline._relative_to(root / "graphml" / "a.graphml", root), "graphml/a.graphml"
        )
        self.assertIsNone(pipeline._relative_to(None, root))


class VerifyBundleTests(_TempDirTestCase):
    def _bundle(self, name: str = "bundle") -> Path:
        out_dir = self.tmp / name
        pipeline.run_pipeline(out_dir=out_dir, demo_count=DEMO_N, seed=0)
        return out_dir

    def _manifest(self, bundle: Path) -> dict:
        return json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))

    def test_a_complete_bundle_passes(self):
        problems, summary = verify.verify_bundle(self._bundle())
        self.assertEqual(problems, [])
        self.assertEqual(summary["records"], DEMO_N)
        self.assertEqual(summary["networks"], len(pipeline.DEFAULT_NETWORK_SPECS))
        self.assertEqual(summary["graphml_files"], 2 * len(pipeline.DEFAULT_NETWORK_SPECS))

    def test_missing_directory(self):
        problems, _ = verify.verify_bundle(self.tmp / "nope")
        self.assertEqual(len(problems), 1)
        self.assertIn("not found", problems[0])

    def test_missing_manifest(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        problems, _ = verify.verify_bundle(empty)
        self.assertTrue(any("manifest.json" in problem for problem in problems))

    def test_corrupt_manifest(self):
        bundle = self._bundle()
        (bundle / "manifest.json").write_text("{not json", encoding="utf-8")
        problems, _ = verify.verify_bundle(bundle)
        self.assertTrue(any("not valid JSON" in problem for problem in problems))

    def test_missing_required_manifest_key(self):
        bundle = self._bundle()
        manifest = self._manifest(bundle)
        del manifest["records"]
        (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        problems, _ = verify.verify_bundle(bundle)
        self.assertTrue(any("'records'" in problem for problem in problems))

    def test_deleted_graphml_is_reported(self):
        bundle = self._bundle()
        (bundle / self._manifest(bundle)["networks"][0]["graphml"]).unlink()
        problems, _ = verify.verify_bundle(bundle)
        self.assertTrue(any("file not found" in problem for problem in problems))

    def test_metrics_row_mismatch_is_reported(self):
        bundle = self._bundle()
        pd.read_csv(bundle / "metrics.csv").iloc[:1].to_csv(
            bundle / "metrics.csv", index=False
        )
        problems, _ = verify.verify_bundle(bundle)
        self.assertTrue(any("data row" in problem for problem in problems))

    def test_empty_summary_is_reported(self):
        bundle = self._bundle()
        (bundle / "summary.txt").write_text("", encoding="utf-8")
        problems, _ = verify.verify_bundle(bundle)
        self.assertTrue(any("summary.txt is empty" in problem for problem in problems))

    def test_skipped_networks_are_reported(self):
        bundle = self._bundle()
        manifest = self._manifest(bundle)
        manifest["skipped_networks"] = ["countries"]
        (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        problems, _ = verify.verify_bundle(bundle)
        self.assertTrue(any("skipped networks" in problem for problem in problems))

    def test_empty_network_is_reported(self):
        bundle = self._bundle()
        manifest = self._manifest(bundle)
        manifest["networks"][0]["edges"] = 0
        (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        problems, _ = verify.verify_bundle(bundle)
        self.assertTrue(any("came out empty" in problem for problem in problems))


class CliTests(_TempDirTestCase):
    """The CLIs are exercised in-process, with their stdout/stderr silenced."""

    @contextlib.contextmanager
    def _quiet(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
            io.StringIO()
        ):
            yield

    def _pipeline(self, argv) -> int:
        with self._quiet():
            return pipeline.main(argv)

    def _verify(self, argv) -> int:
        with self._quiet():
            return verify.main(argv)

    def test_list_specs_exits_zero(self):
        self.assertEqual(self._pipeline(["--list-specs"]), pipeline.EXIT_OK)

    def test_missing_source_is_fatal(self):
        self.assertEqual(self._pipeline(["--out", str(self.tmp / "o")]), pipeline.EXIT_FATAL)

    def test_both_sources_is_fatal(self):
        code = self._pipeline(["--demo", "5", "--input", str(self.tmp / "x.txt")])
        self.assertEqual(code, pipeline.EXIT_FATAL)

    def test_unknown_spec_is_fatal(self):
        code = self._pipeline(["--demo", "5", "--specs", "nope", "--out", str(self.tmp / "o")])
        self.assertEqual(code, pipeline.EXIT_FATAL)

    def test_missing_input_file_is_fatal(self):
        code = self._pipeline(
            ["--input", str(self.tmp / "absent.txt"), "--out", str(self.tmp / "o")]
        )
        self.assertEqual(code, pipeline.EXIT_FATAL)

    def test_demo_run_exits_zero(self):
        code = self._pipeline(["--demo", str(DEMO_N), "--out", str(self.tmp / "cli")])
        self.assertEqual(code, pipeline.EXIT_OK)
        self.assertTrue((self.tmp / "cli" / "manifest.json").is_file())

    def test_bare_demo_flag_uses_the_default_count(self):
        self.assertEqual(self._pipeline(["--demo", "--out", str(self.tmp / "bare")]), 0)
        manifest = json.loads((self.tmp / "bare" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["records"], 60)

    def test_verify_cli_on_a_good_bundle(self):
        self._pipeline(["--demo", str(DEMO_N), "--out", str(self.tmp / "v")])
        self.assertEqual(self._verify([str(self.tmp / "v"), "--quiet"]), verify.EXIT_OK)

    def test_verify_cli_on_a_missing_bundle(self):
        self.assertEqual(self._verify([str(self.tmp / "absent")]), verify.EXIT_MISSING)

    def test_verify_cli_on_an_inconsistent_bundle(self):
        bundle = self.tmp / "bad"
        self._pipeline(["--demo", str(DEMO_N), "--out", str(bundle)])
        (bundle / "summary.txt").unlink()
        self.assertEqual(self._verify([str(bundle)]), verify.EXIT_INVALID)
