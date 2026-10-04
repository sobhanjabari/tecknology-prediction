"""Tests for techintel.wos: tagged-file parsing, C3/country cleanup, CSV loading."""

from __future__ import annotations

import tempfile
import unittest
import warnings
from pathlib import Path

import pandas as pd

from techintel import wos

from .fixtures import (
    WOS_RAW_EXPORT_CSV,
    WOS_RECORD_1,
    WOS_TAGGED_EMPTY,
    WOS_TAGGED_TEXT,
)


class TaggedParserTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _parse(self, text: str = WOS_TAGGED_TEXT) -> pd.DataFrame:
        path = self.tmp / "export.txt"
        path.write_text(text, encoding="utf-8")
        return wos.parse_wos_file(path)

    def test_record_count_and_index(self):
        df = self._parse()
        self.assertEqual(len(df), 2)
        self.assertEqual(list(df["index"]), [1, 2])

    def test_column_order_matches_declaration(self):
        df = self._parse()
        self.assertEqual(list(df.columns), list(wos.WOS_COLUMNS) + ["Countries"])

    def test_vestigial_isi_keywords_column_is_gone(self):
        self.assertNotIn("ISI_Keywords", self._parse().columns)

    def test_simple_fields(self):
        df = self._parse()
        for column, expected in WOS_RECORD_1.items():
            with self.subTest(column=column):
                self.assertEqual(df.loc[0, column], expected)

    def test_multiline_author_field_keeps_commas(self):
        # `AF` continues with "|", so "Piwowar, Heather" is never split apart.
        self.assertEqual(
            self._parse().loc[0, "Authors"], "Piwowar, Heather|Priem, Jason R"
        )

    def test_multiline_keyword_field_joins_with_space(self):
        self.assertEqual(
            self._parse().loc[0, "Keywords"], "open access; bibliometrics; citation analysis"
        )

    def test_multiline_address_field_joins_with_pipe(self):
        self.assertEqual(self._parse().loc[0, "Addresses"], WOS_RECORD_1["Addresses"])

    def test_non_multiline_field_is_not_continued(self):
        # `SO` is not in WOS_MULTILINE_TAGS, so the next line must not extend it.
        text = "PT J\nSO PeerJ\n   stray continuation\nTI T\nER\n"
        self.assertEqual(self._parse(text).loc[0, "Journal"], "PeerJ")

    def test_journal_is_reduced_to_first_comma_segment(self):
        self.assertEqual(self._parse().loc[0, "Journal"], "PeerJ")

    def test_c3_empties_are_dropped(self):
        self.assertEqual(self._parse().loc[0, "C3"], "Univ Washington; OpenAlex")

    def test_countries_are_derived_and_normalised(self):
        df = self._parse()
        self.assertEqual(df.loc[0, "Countries"], "USA ; Canada")
        self.assertEqual(df.loc[1, "Countries"], "Turkiye")

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            wos.parse_wos_file(self.tmp / "nope.txt")

    def test_file_without_records_yields_empty_frame(self):
        df = self._parse(WOS_TAGGED_EMPTY)
        self.assertTrue(df.empty)
        self.assertEqual(list(df.columns), list(wos.WOS_COLUMNS) + ["Countries"])

    def test_empty_file_yields_empty_frame(self):
        self.assertTrue(self._parse("").empty)

    def test_missing_tag_leaves_blank_not_nan(self):
        df = self._parse("PT J\nTI Only a title\nER\n")
        self.assertEqual(df.loc[0, "Abstract"], "")
        self.assertEqual(df.loc[0, "DOI"], "")


class CleanC3Tests(unittest.TestCase):
    def test_drops_empty_items(self):
        self.assertEqual(wos.clean_c3_field("Univ A; ; Univ B;"), "Univ A; Univ B")

    def test_strips_whitespace(self):
        self.assertEqual(wos.clean_c3_field("  Univ A ;  Univ B  "), "Univ A; Univ B")

    def test_nan_and_none_and_blank(self):
        self.assertEqual(wos.clean_c3_field(float("nan")), "")
        self.assertEqual(wos.clean_c3_field(None), "")
        self.assertEqual(wos.clean_c3_field(""), "")

    def test_drop_list_removes_umbrella_names(self):
        # The notebooks shipped this list fully commented out, so the filter
        # never ran; `drop=` is the supported way to enable it.
        self.assertEqual(
            wos.clean_c3_field("Univ A; University of X System; Univ B",
                               drop=["University of X System"]),
            "Univ A; Univ B",
        )

    def test_default_drop_is_empty_and_preserves_names(self):
        self.assertEqual(wos.clean_c3_field("Univ A; University of X System"),
                         "Univ A; University of X System")


class ExtractCountryTests(unittest.TestCase):
    def test_us_address_normalises_to_usa(self):
        # Regression: the original did `parts[-1].strip()[:-1]`, turning this
        # into "WA 98195 US" so the "USA" branch never fired.
        self.assertEqual(
            wos.extract_country_from_address("[Piwowar H] Univ Washington, Seattle, WA 98195 USA"),
            "USA",
        )

    def test_trailing_bracket_is_stripped(self):
        self.assertEqual(
            wos.extract_country_from_address("[Piwowar H] Univ Washington, Seattle, WA 98195 USA]"),
            "USA",
        )

    def test_turkish_address_normalises_to_turkiye(self):
        # Regression: with the eaten final character this never matched either.
        self.assertEqual(
            wos.extract_country_from_address("[Someone E] Bogazici Univ, Istanbul, Turkey"),
            "Turkiye",
        )

    def test_plain_country_is_returned_unchanged(self):
        self.assertEqual(
            wos.extract_country_from_address("[Priem J] OpenAlex, Vancouver, BC, Canada"),
            "Canada",
        )

    def test_single_segment_address(self):
        self.assertEqual(wos.extract_country_from_address("Germany"), "Germany")

    def test_blank_and_missing(self):
        self.assertEqual(wos.extract_country_from_address(None), "")
        self.assertEqual(wos.extract_country_from_address(float("nan")), "")
        self.assertEqual(wos.extract_country_from_address("   "), "")
        self.assertEqual(wos.extract_country_from_address("[]"), "")

    def test_extract_countries_joins_with_semicolon(self):
        self.assertEqual(
            wos.extract_countries(
                "[A] Univ Washington, Seattle, WA 98195 USA|[B] OpenAlex, Vancouver, BC, Canada"
            ),
            "USA ; Canada",
        )

    def test_extract_countries_skips_blank_blocks(self):
        self.assertEqual(wos.extract_countries("[A] Berlin, Germany||  "), "Germany")

    def test_extract_countries_blank_input(self):
        self.assertEqual(wos.extract_countries(None), "")
        self.assertEqual(wos.extract_countries(float("nan")), "")


class ReadWosCsvTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _write(self, text: str, name: str = "export.csv") -> Path:
        path = self.tmp / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_missing_file_raises_with_guidance(self):
        with self.assertRaises(FileNotFoundError) as ctx:
            wos.read_wos_csv(self.tmp / "absent.csv")
        self.assertIn("Input file not found", str(ctx.exception))

    def test_header_only_file_raises_value_error(self):
        path = self._write("Title,Abstract\n")
        with self.assertRaises(ValueError) as ctx:
            wos.read_wos_csv(path)
        self.assertIn("contains no rows", str(ctx.exception))

    def test_none_of_expected_columns_raises(self):
        path = self._write("Foo,Bar\n1,2\n")
        with self.assertRaises(ValueError) as ctx:
            wos.read_wos_csv(path, expected_columns=["Title", "Abstract"])
        self.assertIn("None of the expected text columns", str(ctx.exception))

    def test_partially_missing_columns_warn_but_succeed(self):
        path = self._write("Title,Abstract\nHello,World\n")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            df = wos.read_wos_csv(path, expected_columns=["Title", "Keywords"])
        self.assertEqual(len(df), 1)
        self.assertEqual([str(w.message) for w in caught], [
            "Expected column(s) not found and will be skipped: ['Keywords']"
        ])

    def test_no_expected_columns_skips_validation(self):
        path = self._write("Foo\n1\n")
        df = wos.read_wos_csv(path)
        self.assertEqual(list(df.columns), ["Foo"])

    def test_happy_path_reads_all_rows(self):
        path = self._write("Title,Abstract\nA,x\nB,y\n")
        df = wos.read_wos_csv(path, expected_columns=["Title"])
        self.assertEqual(len(df), 2)
        self.assertEqual(list(df["Title"]), ["A", "B"])

    def test_utf8_bom_is_tolerated(self):
        path = self.tmp / "bom.csv"
        path.write_text("Title\nCaf\u00e9\n", encoding="utf-8-sig")
        df = wos.read_wos_csv(path, expected_columns=["Title"])
        self.assertEqual(df.loc[0, "Title"], "Caf\u00e9")


class SaveToExcelTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_writes_xlsx_when_openpyxl_available(self):
        try:
            import openpyxl  # noqa: F401
        except ImportError:
            self.skipTest("openpyxl is not installed")
        target = self.tmp / "out.xlsx"
        written = wos.save_to_excel(pd.DataFrame({"a": [1]}), target)
        self.assertEqual(written, target)
        self.assertTrue(target.exists())

    def test_missing_openpyxl_raises_actionable_import_error(self):
        try:
            import openpyxl  # noqa: F401
        except ImportError:
            with self.assertRaises(ImportError) as ctx:
                wos.save_to_excel(pd.DataFrame({"a": [1]}), self.tmp / "out.xlsx")
            self.assertIn("openpyxl", str(ctx.exception))
        else:
            self.skipTest("openpyxl is installed, so the fallback cannot be exercised")


class NormalizeWosColumnsTests(unittest.TestCase):
    """A raw WoS "Full Record" export must end up on the pipeline's schema."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _raw(self) -> pd.DataFrame:
        path = self.tmp / "raw_export.csv"
        path.write_text(WOS_RAW_EXPORT_CSV, encoding="utf-8")
        return wos.read_wos_csv(path, sep=",")

    def test_raw_export_is_detected(self):
        self.assertTrue(wos.is_raw_wos_export(self._raw()))

    def test_a_normalised_frame_is_not_mistaken_for_a_raw_export(self):
        self.assertFalse(wos.is_raw_wos_export(pd.DataFrame({"Authors": ["a"]})))

    def test_every_network_column_is_produced(self):
        normalised = wos.normalize_wos_columns(self._raw())
        for column in ("Authors", "Keywords", "Subject_Category", "Countries", "C3"):
            with self.subTest(column=column):
                self.assertIn(column, normalised.columns)

    def test_long_headers_are_renamed(self):
        normalised = wos.normalize_wos_columns(self._raw())
        self.assertEqual(normalised["Year"].tolist(), [2018, 2019])
        self.assertEqual(normalised["Title"].iloc[0], "Open access matters")
        self.assertEqual(normalised["Journal"].iloc[0], "PeerJ")
        self.assertEqual(
            normalised["Subject_Category"].iloc[0],
            "Information Science; Decision Sciences",
        )

    def test_author_lists_are_rejoined_with_a_pipe(self):
        # The raw export uses ";", but the authors network splits on "|". Without
        # this the whole author list becomes one node and the graph has no edges.
        normalised = wos.normalize_wos_columns(self._raw())
        self.assertEqual(normalised["Authors"].iloc[0], "Piwowar, H|Priem, J")

    def test_address_blocks_are_rejoined_and_used_for_countries(self):
        normalised = wos.normalize_wos_columns(self._raw())
        self.assertEqual(normalised["Countries"].iloc[0], "USA ; Canada")
        self.assertEqual(
            normalised["Addresses"].iloc[0],
            "[Piwowar, Heather] Univ Washington, Seattle, WA 98195 USA|"
            "[Priem, Jason] OpenAlex, Vancouver, BC, Canada",
        )

    def test_c3_prefers_the_affiliations_column(self):
        normalised = wos.normalize_wos_columns(self._raw())
        self.assertEqual(normalised["C3"].iloc[0], "University of Washington")

    def test_c3_is_derived_from_addresses_when_affiliations_is_absent(self):
        raw = self._raw().drop(columns=["Affiliations"])
        normalised = wos.normalize_wos_columns(raw)
        self.assertEqual(normalised["C3"].iloc[0], "Univ Washington; OpenAlex")

    def test_keywords_fall_back_to_keywords_plus(self):
        raw = pd.DataFrame({"Keywords Plus": ["alpha; beta"], "Publication Type": ["J"]})
        normalised = wos.normalize_wos_columns(raw)
        self.assertEqual(normalised["Keywords"].iloc[0], "alpha; beta")

    def test_existing_columns_are_left_alone(self):
        # An already-normalised frame must pass through unchanged, so the tagged
        # path is unaffected.
        original = pd.DataFrame({"Authors": ["A|B"], "Keywords": ["k1; k2"]})
        normalised = wos.normalize_wos_columns(original)
        self.assertEqual(normalised["Authors"].iloc[0], "A|B")
        self.assertEqual(normalised["Keywords"].iloc[0], "k1; k2")
        self.assertEqual(list(normalised.columns), ["Authors", "Keywords"])

    def test_input_frame_is_not_mutated(self):
        raw = self._raw()
        before = list(raw.columns)
        wos.normalize_wos_columns(raw)
        self.assertEqual(list(raw.columns), before)

    def test_pipeline_loads_a_raw_export_and_builds_every_network(self):
        from techintel import pipeline

        path = self.tmp / "raw_export.csv"
        path.write_text(WOS_RAW_EXPORT_CSV, encoding="utf-8")
        built, skipped = pipeline.build_networks(pipeline.load_records(path))
        self.assertEqual(skipped, [])
        self.assertEqual(set(built), {spec.name for spec in pipeline.DEFAULT_NETWORK_SPECS})
        # The authors network is only non-trivial if the ";" -> "|" split worked.
        self.assertEqual(built["authors"].number_of_nodes(), 3)
        self.assertEqual(built["authors"].number_of_edges(), 1)


class ExtractOrganisationTests(unittest.TestCase):
    def test_bracketed_author_list_is_stripped(self):
        block = "[Piwowar, Heather; Priem, Jason] Univ Washington, Seattle, WA 98195 USA"
        self.assertEqual(
            wos.extract_organisation_from_address(block), "Univ Washington"
        )

    def test_block_without_brackets_still_works(self):
        self.assertEqual(
            wos.extract_organisation_from_address("OpenAlex, Vancouver, Canada"),
            "OpenAlex",
        )

    def test_blank_and_missing_input_yield_an_empty_string(self):
        for value in (None, float("nan"), "", "   "):
            with self.subTest(value=value):
                self.assertEqual(wos.extract_organisation_from_address(value), "")

    def test_organisations_are_joined_per_block(self):
        addresses = "[A] Univ Washington, Seattle, USA|[B] OpenAlex, Vancouver, Canada"
        self.assertEqual(wos.extract_organisations(addresses), "Univ Washington; OpenAlex")

    def test_blank_addresses_yield_an_empty_string(self):
        self.assertEqual(wos.extract_organisations(None), "")


if __name__ == "__main__":
    unittest.main()


