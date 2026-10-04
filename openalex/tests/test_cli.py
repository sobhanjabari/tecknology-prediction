"""Tests for openalex_cli.py: flag handling, streaming output, and end-to-end runs.

The API client is replaced with `FakeClient`, so nothing here touches the network.
"""

from __future__ import annotations

import contextlib
import csv
import io
import json
import os
import tempfile
import tracemalloc
import unittest
from unittest import mock

import openalex_cli as cli

from .fixtures import SAMPLE_WORK, FakeClient, sample_works


def run_cli(argv, client=None, **client_kwargs):
    """Run `main(argv)` with a stubbed client; return (exit_code, stdout, stderr)."""
    client = client if client is not None else FakeClient(records=sample_works(3), total=3, **client_kwargs)
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(cli, "make_client", return_value=client), contextlib.redirect_stdout(
        out
    ), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


@contextlib.contextmanager
def temp_csv():
    """Yield a path inside a throwaway directory."""
    with tempfile.TemporaryDirectory() as tmp:
        yield os.path.join(tmp, "out.csv")


def read_csv(path):
    """Parse a written CSV; returns (rows, header) using a real CSV reader."""
    with open(path, encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle)), header


class NormalizeDateTests(unittest.TestCase):
    def test_year_only(self):
        self.assertEqual(cli.normalize_date("2015", upper=False), "2015-01-01")
        self.assertEqual(cli.normalize_date("2015", upper=True), "2015-12-31")

    def test_year_month(self):
        self.assertEqual(cli.normalize_date("2015-06", upper=False), "2015-06-01")
        self.assertEqual(cli.normalize_date("2015-06", upper=True), "2015-06-30")

    def test_full_date_passes_through(self):
        self.assertEqual(cli.normalize_date("2015-06-15", upper=False), "2015-06-15")


class BuildFilterTests(unittest.TestCase):
    def args(self, **kwargs):
        base = {"filter": None, "since": None, "until": None}
        base.update(kwargs)
        return cli.argparse.Namespace(**base)

    def test_none_when_nothing_given(self):
        self.assertIsNone(cli.build_filters(self.args(), "works"))

    def test_filter_only(self):
        self.assertEqual(cli.build_filters(self.args(filter="is_oa:true"), "works"), "is_oa:true")

    def test_since_and_until_are_converted(self):
        got = cli.build_filters(self.args(since="2015", until="2020"), "works")
        self.assertEqual(got, "from_publication_date:2015-01-01,to_publication_date:2020-12-31")

    def test_merged_with_explicit_filter(self):
        got = cli.build_filters(self.args(filter="is_oa:true", since="2015"), "works")
        self.assertEqual(got, "is_oa:true,from_publication_date:2015-01-01")

    def test_rejected_for_non_works(self):
        with self.assertRaises(cli.CliError):
            cli.build_filters(self.args(since="2015"), "authors")


class PerPageTests(unittest.TestCase):
    def test_clamps_and_warns(self):
        args = cli.argparse.Namespace(per_page=9999)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(cli.resolve_per_page(args, cli.Reporter()), cli.MAX_PER_PAGE)
        self.assertIn("exceeds the API maximum", err.getvalue())

    def test_rejects_zero(self):
        args = cli.argparse.Namespace(per_page=0)
        with self.assertRaises(cli.CliError):
            cli.resolve_per_page(args, cli.Reporter(quiet=True))

    def test_quiet_suppresses_the_warning(self):
        args = cli.argparse.Namespace(per_page=9999)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            cli.resolve_per_page(args, cli.Reporter(quiet=True))
        self.assertEqual(err.getvalue(), "")


class DedupeTests(unittest.TestCase):
    def test_drops_repeats_by_id(self):
        records = [{"id": "A"}, {"id": "A"}, {"id": "B"}]
        self.assertEqual([r["id"] for r in cli.dedupe(records)], ["A", "B"])

    def test_falls_back_to_doi(self):
        records = [{"doi": "10.1/x"}, {"doi": "10.1/x"}, {"doi": "10.1/y"}]
        self.assertEqual(len(list(cli.dedupe(records))), 2)


class AsciiTests(unittest.TestCase):
    def test_accents_are_transliterated(self):
        self.assertEqual(cli.asciify("naïve ünïcode"), "naive unicode")

    def test_typographic_punctuation_is_mapped(self):
        self.assertEqual(cli.asciify("Met… “q” — x"), 'Met... "q" - x')

    def test_truncation_marker_is_ascii_in_ascii_mode(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.print_table([{"t": "x" * 60}], ["t"], width=20, ascii_only=True)
        self.assertIn("...", out.getvalue())
        self.assertNotIn("…", out.getvalue())

    def test_unicode_marker_otherwise(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.print_table([{"t": "x" * 60}], ["t"], width=20, ascii_only=False)
        self.assertIn("…", out.getvalue())


class ConfigTests(unittest.TestCase):
    def test_reads_key_value_pairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, ".openalexrc")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("# comment\nmailto = me@example.com\napi_key=secret\n\n")
            self.assertEqual(cli.load_config(path), {"mailto": "me@example.com", "api_key": "secret"})

    def test_missing_file_returns_empty(self):
        self.assertEqual(cli.load_config(os.path.join(tempfile.gettempdir(), "nope-xyz", ".openalexrc")), {})


class ParseSelectTests(unittest.TestCase):
    def test_splits_and_strips(self):
        args = cli.argparse.Namespace(select=" id , title ,, doi ")
        self.assertEqual(cli.parse_select(args), ["id", "title", "doi"])

    def test_none_when_absent(self):
        self.assertIsNone(cli.parse_select(cli.argparse.Namespace(select=None)))


class StreamingOutputTests(unittest.TestCase):
    def test_write_csv_does_not_buffer_the_result_set(self):
        # Regression: write_csv used to do `list(rows)`, so `--limit 0` on a large
        # harvest would exhaust memory. 50k rows would be >10 MB if buffered.
        def rows():
            for index in range(50000):
                yield {"a": index, "b": "x" * 100}

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "big.csv")
            tracemalloc.start()
            count = cli.write_csv(rows(), path, reporter=cli.Reporter(quiet=True))
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            self.assertEqual(count, 50000)
            self.assertLess(peak, 5_000_000, f"peak was {peak} bytes; output is not streaming")
            self.assertEqual(sum(1 for _ in open(path, encoding="utf-8-sig")), 50001)

    def test_write_csv_uses_explicit_field_order(self):
        with temp_csv() as path:
            cli.write_csv([{"b": 1, "a": 2}], path, fields=["a", "b"], reporter=cli.Reporter(quiet=True))
            _, header = read_csv(path)
            self.assertEqual(header, ["a", "b"])

    def test_write_csv_fills_missing_keys_with_empty(self):
        with temp_csv() as path:
            cli.write_csv([{"a": 1, "b": 2}, {"a": 3}], path, reporter=cli.Reporter(quiet=True))
            rows, _ = read_csv(path)
            self.assertEqual(rows[1], {"a": "3", "b": ""})

    def test_write_csv_on_empty_input_writes_nothing(self):
        with temp_csv() as path:
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(cli.write_csv([], path, reporter=cli.Reporter()), 0)
            self.assertFalse(os.path.exists(path))
            self.assertIn("no results", err.getvalue())

    def test_write_json_stream_is_valid_json(self):
        out = io.StringIO()
        count = cli.write_json_stream(iter([{"a": 1}, {"a": 2}]), out)
        self.assertEqual(count, 2)
        self.assertEqual(json.loads(out.getvalue()), [{"a": 1}, {"a": 2}])

    def test_write_json_stream_empty(self):
        out = io.StringIO()
        self.assertEqual(cli.write_json_stream(iter([]), out), 0)
        self.assertEqual(json.loads(out.getvalue()), [])

    def test_write_jsonl_one_record_per_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "r.jsonl")
            cli.write_jsonl(iter([{"a": 1}, {"a": 2}]), path, reporter=cli.Reporter(quiet=True))
            lines = [line for line in open(path, encoding="utf-8") if line.strip()]
            self.assertEqual([json.loads(line) for line in lines], [{"a": 1}, {"a": 2}])

    def test_write_xlsx_without_openpyxl_is_a_clean_error(self):
        try:
            import openpyxl  # noqa: F401
        except ImportError:
            pass
        else:
            self.skipTest("openpyxl is installed")
        with temp_csv() as path:
            with self.assertRaises(cli.CliError):
                cli.write_xlsx([{"a": 1}], path, reporter=cli.Reporter(quiet=True))



class SearchCommandTests(unittest.TestCase):
    def test_select_projection_drops_unselected_columns(self):
        # Regression: --select used to emit all 35 columns, with fabricated 0/""
        # values for every field the API had not returned.
        with temp_csv() as path:
            code, _, _ = run_cli(
                ["search", "works", "-q", "graphene", "--select", "id,title", "--csv", path, "--limit", "3"]
            )
            self.assertEqual(code, 0)
            rows, header = read_csv(path)
            self.assertEqual(header, ["id", "title"])
            self.assertEqual(len(rows), 3)

    def test_select_accepts_openalex_field_names(self):
        with temp_csv() as path:
            run_cli(["search", "works", "--select", "display_name,publication_year", "--csv", path, "--quiet"])
            _, header = read_csv(path)
            self.assertEqual(header, ["title", "publication_year"])

    def test_select_warns_about_unmapped_fields(self):
        with temp_csv() as path:
            _, _, err = run_cli(["search", "works", "--select", "id,nonsense", "--csv", path])
            self.assertIn("nonsense", err)

    def test_limit_zero_means_everything(self):
        client = FakeClient(records=sample_works(4), total=4)
        code, _, _ = run_cli(["search", "works", "--limit", "0", "--quiet"], client=client)
        self.assertEqual(code, 0)
        self.assertIsNone(client.calls[1]["limit"])

    def test_since_becomes_a_date_filter(self):
        client = FakeClient(records=sample_works(1), total=1)
        run_cli(["search", "works", "--since", "2015", "--quiet"], client=client)
        self.assertIn("from_publication_date:2015-01-01", client.calls[0]["filters"])

    def test_quiet_silences_stderr(self):
        _, _, err = run_cli(["search", "works", "--limit", "2", "--quiet"])
        self.assertEqual(err, "")

    def test_count_prints_the_number(self):
        client = FakeClient(records=[], total=1234)
        code, out, _ = run_cli(["count", "works", "--quiet"], client=client)
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "1234")

    def test_group_by_writes_csv(self):
        with temp_csv() as path:
            run_cli(["group-by", "works", "publication_year", "--csv", path, "--quiet"])
            rows, header = read_csv(path)
            self.assertEqual(header, ["key", "count"])
            self.assertEqual(rows[0]["key"], "2018")

    def test_autocomplete_writes_csv(self):
        with temp_csv() as path:
            run_cli(["autocomplete", "institutions", "sharif", "--csv", path, "--quiet"])
            rows, header = read_csv(path)
            self.assertEqual(header, ["id", "name", "works_count", "cited_by_count", "hint"])
            self.assertEqual(rows[0]["name"], "Sharif University")

    def test_get_writes_csv(self):
        with temp_csv() as path:
            run_cli(["get", "works", "W2741809807", "--csv", path, "--quiet"])
            rows, _ = read_csv(path)
            self.assertEqual(rows[0]["title"], "The state of OA")

    def test_json_output_is_streamed_and_valid(self):
        code, out, _ = run_cli(["search", "works", "--json", "--limit", "2", "--quiet"])
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(out)), 2)

    def test_dedupe_removes_repeats(self):
        repeated = [SAMPLE_WORK, SAMPLE_WORK, dict(SAMPLE_WORK, id="https://openalex.org/W9")]
        client = FakeClient(records=repeated, total=3)
        with temp_csv() as path:
            run_cli(["search", "works", "--dedupe", "--csv", path, "--quiet"], client=client)
            rows, _ = read_csv(path)
            self.assertEqual(len(rows), 2)



class HarvestCommandTests(unittest.TestCase):
    def test_short_schema_writes_notebook_columns(self):
        with temp_csv() as path:
            code, _, _ = run_cli(["harvest", "works", "-q", "graphene", "--limit", "2", "--csv", path, "--quiet"])
            self.assertEqual(code, 0)
            rows, header = read_csv(path)
            self.assertEqual(
                header,
                ["Abstract", "Authors", "C3", "DOI", "Funding", "Journal", "Keywords",
                 "Year", "Addresses", "Title", "Cite", "Subject_Category", "Countries"],
            )
            self.assertEqual(rows[0]["Year"], "2018")
            self.assertEqual(rows[0]["Abstract"], "Open access matters")

    def test_wos_schema_writes_long_field_names(self):
        client = FakeClient(records=[SAMPLE_WORK], total=1)
        with temp_csv() as path:
            run_cli(
                ["harvest", "works", "--schema", "wos", "--limit", "1", "--csv", path, "--quiet"],
                client=client,
            )
            rows, header = read_csv(path)
            self.assertEqual(len(header), 32)
            self.assertIn("Article Title", header)
            self.assertIn("UT (Unique WOS ID)", header)
            self.assertEqual(rows[0]["UT (Unique WOS ID)"], "W2741809807")
            self.assertEqual(rows[0]["Times Cited, WoS Core"], "1260")

    def test_entity_positional_is_optional(self):
        with temp_csv() as path:
            code, _, _ = run_cli(["harvest", "-q", "x", "--limit", "1", "--csv", path, "--quiet"])
            self.assertEqual(code, 0)

    def test_no_abstract_blanks_the_column(self):
        with temp_csv() as path:
            run_cli(["harvest", "works", "--no-abstract", "--limit", "1", "--csv", path, "--quiet"])
            rows, _ = read_csv(path)
            self.assertEqual(rows[0]["Abstract"], "")

    def test_require_abstract_adds_a_filter(self):
        client = FakeClient(records=sample_works(1), total=1)
        with temp_csv() as path:
            run_cli(["harvest", "works", "--require-abstract", "--limit", "1", "--csv", path, "--quiet"], client=client)
        self.assertIn("has_abstract:true", client.calls[0]["filters"])

    def test_default_csv_path_is_out_harvest(self):
        parser = cli.build_parser()
        args = parser.parse_args(["harvest", "works"])
        self.assertEqual(args.csv, os.path.join("out", "harvest.csv"))

    def test_dedupe_applies(self):
        repeated = [SAMPLE_WORK, SAMPLE_WORK]
        client = FakeClient(records=repeated, total=2)
        with temp_csv() as path:
            run_cli(["harvest", "works", "--dedupe", "--csv", path, "--quiet"], client=client)
            rows, _ = read_csv(path)
            self.assertEqual(len(rows), 1)


class ReferencesCommandTests(unittest.TestCase):
    def test_limit_caps_output_rows(self):
        # Regression: `--limit` was ignored, so `--limit 3` wrote every reference.
        references = [dict(SAMPLE_WORK, id=f"https://openalex.org/W{index}") for index in range(10)]
        client = FakeClient(records=references, total=10)
        with temp_csv() as path:
            code, _, _ = run_cli(["references", "W2741809807", "--limit", "3", "--csv", path, "--quiet"], client=client)
            self.assertEqual(code, 0)
            rows, _ = read_csv(path)
            self.assertEqual(len(rows), 3)

    def test_max_refs_caps_the_lookups(self):
        client = FakeClient(records=sample_works(1), total=1)
        with temp_csv() as path:
            run_cli(
                ["references", "W2741809807", "--max-refs", "1", "--csv", path, "--quiet"],
                client=client,
            )
        # SAMPLE_WORK has two referenced_works; --max-refs 1 keeps one batch of one id.
        search_call = [c for c in client.calls if c["method"] == "search"][0]
        self.assertEqual(search_call["filters"], "openalex_id:W1")


class FlagPositionTests(unittest.TestCase):
    def test_quiet_accepted_before_the_command(self):
        code, _, err = run_cli(["--quiet", "search", "works", "--limit", "1"])
        self.assertEqual(code, 0)
        self.assertEqual(err, "")

    def test_quiet_accepted_after_the_command(self):
        code, _, err = run_cli(["search", "works", "--limit", "1", "--quiet"])
        self.assertEqual(code, 0)
        self.assertEqual(err, "")

    def test_mailto_accepted_after_the_command(self):
        parser = cli.build_parser()
        self.assertEqual(parser.parse_args(["search", "works", "--mailto", "a@b.co"]).mailto, "a@b.co")
        self.assertEqual(parser.parse_args(["--mailto", "a@b.co", "search", "works"]).mailto, "a@b.co")

    def test_version_flag_exits(self):
        with self.assertRaises(SystemExit) as caught:
            cli.build_parser().parse_args(["--version"])
        self.assertEqual(caught.exception.code, 0)

    def test_unknown_entity_is_rejected_by_the_parser(self):
        with self.assertRaises(SystemExit):
            cli.build_parser().parse_args(["search", "bogus"])


class ErrorHandlingTests(unittest.TestCase):
    def test_openalex_error_becomes_exit_code_2(self):
        from openalex_client import OpenAlexError

        client = mock.Mock()
        client.count.side_effect = OpenAlexError("boom")
        code, _, err = run_cli(["search", "works", "--quiet"], client=client)
        self.assertEqual(code, 2)
        self.assertIn("boom", err)

    def test_cli_error_becomes_exit_code_2(self):
        code, _, err = run_cli(["search", "authors", "--since", "2015", "--quiet"])
        self.assertEqual(code, 2)
        self.assertIn("--since", err)


if __name__ == "__main__":
    unittest.main()

