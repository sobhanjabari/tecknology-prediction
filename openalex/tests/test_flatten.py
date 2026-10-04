"""Tests for flatten.py: abstract rebuilding, projection, and WoS schemas."""

from __future__ import annotations

import unittest

from flatten import (
    HARVEST_SCHEMAS,
    WOS_LONG_COLUMNS,
    WOS_SHORT_COLUMNS,
    _abstract,
    _join,
    _uniquify,
    flatten,
    flatten_generic,
    flatten_work,
    flatten_wos,
    project,
)

from .fixtures import SAMPLE_WORK


class AbstractTests(unittest.TestCase):
    def test_rebuilds_in_positional_order(self):
        record = {"abstract_inverted_index": {"world": [1], "Hello": [0], "again": [2]}}
        self.assertEqual(_abstract(record), "Hello world again")

    def test_word_appearing_twice(self):
        self.assertEqual(_abstract({"abstract_inverted_index": {"a": [0, 2], "b": [1]}}), "a b a")

    def test_empty_when_absent(self):
        self.assertEqual(_abstract({}), "")
        self.assertEqual(_abstract({"abstract_inverted_index": None}), "")

    def test_falls_back_to_plain_abstract(self):
        self.assertEqual(_abstract({"abstract": "plain text"}), "plain text")

    def test_malformed_index_falls_back(self):
        record = {"abstract_inverted_index": "nope", "abstract": "fallback"}
        self.assertEqual(_abstract(record), "fallback")


class JoinTests(unittest.TestCase):
    def test_truncation_counts_only_cleaned_values(self):
        # Regression: "...(+N)" used to count blanks/None in the total.
        self.assertEqual(_join(["a", None, "", "b", "c", "d"], limit=2), "a; b; ...(+2)")

    def test_unlimited(self):
        self.assertEqual(_join(["a", "b"]), "a; b")

    def test_empty(self):
        self.assertEqual(_join([]), "")


class UniquifyTests(unittest.TestCase):
    def test_preserves_order_and_drops_blanks(self):
        self.assertEqual(_uniquify(["b", "a", "b", "", None, "   ", "a"]), ["b", "a"])

    def test_case_insensitive_but_keeps_first_spelling(self):
        self.assertEqual(_uniquify(["Bibliometrics", "bibliometrics", "Citation"]), ["Bibliometrics", "Citation"])


class FlattenWorkTests(unittest.TestCase):
    def test_core_scalars(self):
        row = flatten_work(SAMPLE_WORK)
        self.assertEqual(row["id"], "W2741809807")
        self.assertEqual(row["title"], "The state of OA")
        self.assertEqual(row["publication_year"], 2018)
        self.assertEqual(row["cited_by_count"], 1260)
        self.assertEqual(row["source"], "PeerJ")
        self.assertEqual(row["publisher"], "PeerJ, Inc.")
        self.assertEqual(row["citation_percentile"], 0.99965)
        self.assertEqual(row["referenced_works_count"], 2)
        self.assertEqual(row["authors_count"], 2)

    def test_institutions_and_countries_deduplicated(self):
        row = flatten_work(SAMPLE_WORK)
        self.assertEqual(row["institutions"], "University of Washington; OpenAlex")
        self.assertEqual(row["countries"], "CA; US")

    def test_corresponding_authors(self):
        self.assertEqual(flatten_work(SAMPLE_WORK)["corresponding_authors"], "Heather Piwowar")

    def test_abstract_only_when_requested(self):
        self.assertNotIn("abstract", flatten_work(SAMPLE_WORK))
        self.assertEqual(flatten_work(SAMPLE_WORK, with_abstract=True)["abstract"], "Open access matters")

    def test_empty_record_does_not_crash(self):
        row = flatten_work({})
        self.assertEqual(row["id"], "")
        self.assertEqual(row["cited_by_count"], 0)
        self.assertEqual(row["authors_count"], 0)

    def test_column_order_is_stable(self):
        # write_csv takes its header from the first row, so key order must not vary.
        without = list(flatten_work(SAMPLE_WORK))
        with_abstract = list(flatten_work(SAMPLE_WORK, with_abstract=True))
        self.assertEqual(with_abstract[:-1], without)
        self.assertEqual(with_abstract[-1], "abstract")


class FlattenGenericTests(unittest.TestCase):
    def test_bare_lists_always_become_strings(self):
        # Regression: bare lists used to yield ints, mixing types in one CSV column.
        row = flatten_generic({"id": "https://openalex.org/X1", "plain": [1, 2, 3]})
        self.assertEqual(row["id"], "X1")
        self.assertEqual(row["plain"], "3")
        self.assertIsInstance(row["plain"], str)

    def test_named_lists_join_display_names(self):
        row = flatten_generic({"things": [{"display_name": "A"}, {"display_name": "B"}]})
        self.assertEqual(row["things"], "A; B")

    def test_noisy_keys_dropped(self):
        row = flatten_generic({"abstract_inverted_index": {"x": [0]}, "counts_by_year": [], "ok": 1})
        self.assertNotIn("abstract_inverted_index", row)
        self.assertNotIn("counts_by_year", row)
        self.assertEqual(row["ok"], 1)


class FlattenDispatchTests(unittest.TestCase):
    def test_works_dispatches_to_flatten_work(self):
        self.assertIn("publication_year", flatten("works", SAMPLE_WORK))

    def test_author_entity(self):
        row = flatten("authors", {"id": "https://openalex.org/A1", "display_name": "A", "summary_stats": {"h_index": 5}})
        self.assertEqual(row["name"], "A")
        self.assertEqual(row["h_index"], 5)

    def test_unknown_entity_falls_back_to_generic(self):
        row = flatten("domains", {"id": "https://openalex.org/D1", "x": 2})
        self.assertEqual(row["id"], "D1")
        self.assertEqual(row["x"], 2)


class ProjectTests(unittest.TestCase):
    """`project()` backs `--select`: it must keep exactly what was asked for."""

    def setUp(self):
        self.row = flatten_work(SAMPLE_WORK)

    def test_accepts_flat_column_names(self):
        projected, unmatched = project(self.row, ["id", "title"])
        self.assertEqual(projected, {"id": "W2741809807", "title": "The state of OA"})
        self.assertEqual(unmatched, [])

    def test_accepts_openalex_field_names(self):
        projected, unmatched = project(self.row, ["display_name", "publication_year"])
        self.assertEqual(projected, {"title": "The state of OA", "publication_year": 2018})
        self.assertEqual(unmatched, [])

    def test_does_not_invent_values_for_unselected_fields(self):
        # The whole point: no fabricated 0/"" columns for fields the API skipped.
        projected, _ = project(self.row, ["id", "cited_by_count"])
        self.assertEqual(set(projected), {"id", "cited_by_count"})

    def test_expands_compound_fields(self):
        projected, _ = project(self.row, ["authorships"])
        self.assertEqual(
            set(projected),
            {"authors_count", "authors", "corresponding_authors", "institutions", "countries"},
        )

    def test_expands_open_access_fields(self):
        projected, _ = project(self.row, ["open_access"])
        self.assertEqual(set(projected), {"is_oa", "oa_status", "oa_url"})

    def test_preserves_requested_order(self):
        projected, _ = project(self.row, ["title", "id"])
        self.assertEqual(list(projected), ["title", "id"])

    def test_reports_unknown_fields(self):
        projected, unmatched = project(self.row, ["id", "nonsense"])
        self.assertEqual(projected, {"id": "W2741809807"})
        self.assertEqual(unmatched, ["nonsense"])

    def test_blank_entries_are_ignored(self):
        projected, unmatched = project(self.row, ["id", " ", ""])
        self.assertEqual(projected, {"id": "W2741809807"})
        self.assertEqual(unmatched, [])

    def test_empty_selection_keeps_nothing(self):
        projected, unmatched = project(self.row, [])
        self.assertEqual(projected, {})
        self.assertEqual(unmatched, [])


class HarvestSchemaTests(unittest.TestCase):
    def test_short_schema_has_the_documented_columns_in_order(self):
        self.assertEqual(list(flatten_wos(SAMPLE_WORK, schema="short")), list(WOS_SHORT_COLUMNS))

    def test_wos_schema_has_the_documented_columns_in_order(self):
        self.assertEqual(list(flatten_wos(SAMPLE_WORK, schema="wos")), list(WOS_LONG_COLUMNS))

    def test_short_schema_values(self):
        row = flatten_wos(SAMPLE_WORK, schema="short")
        self.assertEqual(row["Abstract"], "Open access matters")
        self.assertEqual(row["Authors"], "Heather Piwowar; Jason R Priem")
        self.assertEqual(row["C3"], "University of Washington, Seattle, USA; OpenAlex, USA")
        self.assertEqual(row["DOI"], "https://doi.org/10.7717/peerj.4375")
        self.assertEqual(row["Funding"], "NSF 12345")
        self.assertEqual(row["Journal"], "PeerJ")
        self.assertEqual(row["Keywords"], "open access; bibliometrics; Citation")
        self.assertEqual(row["Year"], 2018)
        self.assertEqual(row["Addresses"], "University of Washington, US; OpenAlex, US")
        self.assertEqual(row["Title"], "The state of OA")
        self.assertEqual(row["Cite"], 1260)
        self.assertEqual(row["Subject_Category"], "Decision Sciences; Scientometrics; Open Access")
        self.assertEqual(row["Countries"], "CA; US")

    def test_wos_schema_values(self):
        row = flatten_wos(SAMPLE_WORK, schema="wos")
        self.assertEqual(row["Article Title"], "The state of OA")
        self.assertEqual(row["Source Title"], "PeerJ")
        self.assertEqual(row["Author Keywords"], "open access; bibliometrics")
        self.assertEqual(row["Keywords Plus"], "Citation; Bibliometrics")
        self.assertEqual(row["Cited Reference Count"], 2)
        self.assertEqual(row["Times Cited, WoS Core"], 1260)
        self.assertEqual(row["Times Cited, All Databases"], 1260)
        self.assertEqual(row["UT (Unique WOS ID)"], "W2741809807")
        self.assertEqual(row["Open Access Designations"], "gold")
        self.assertEqual(row["Reprint Addresses"], "Heather Piwowar")
        self.assertEqual(row["Volume"], "6")
        self.assertEqual(row["Start Page"], "e4375")
        self.assertEqual(row["Research Areas"], "Decision Sciences")

    def test_c3_falls_back_to_institutions_without_raw_affiliations(self):
        record = dict(SAMPLE_WORK)
        record["authorships"] = [
            {**SAMPLE_WORK["authorships"][0], "raw_affiliation_strings": []}
        ]
        self.assertEqual(flatten_wos(record, schema="short")["C3"], "University of Washington")

    def test_abstract_can_be_disabled(self):
        self.assertEqual(flatten_wos(SAMPLE_WORK, schema="short", with_abstract=False)["Abstract"], "")

    def test_unknown_schema_raises(self):
        with self.assertRaises(ValueError):
            flatten_wos(SAMPLE_WORK, schema="nope")

    def test_registry_matches_the_constants(self):
        self.assertIs(HARVEST_SCHEMAS["short"], WOS_SHORT_COLUMNS)
        self.assertIs(HARVEST_SCHEMAS["wos"], WOS_LONG_COLUMNS)

    def test_empty_record_does_not_crash(self):
        row = flatten_wos({}, schema="short")
        self.assertEqual(set(row), set(WOS_SHORT_COLUMNS))
        self.assertEqual(row["Cite"], 0)


if __name__ == "__main__":
    unittest.main()

