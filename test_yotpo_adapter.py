"""
Tests for yotpo_adapter.py. No network access — fixtures/yotpo_export_sample.csv
is a trimmed, real slice of a manually-exported Yotpo CSV (5 rows from
reviews/yotpo_sample.csv): two deleted+escalated rows, one deleted+escalated
duplicate, and two clean rows, so it exercises both the completeness guard
and the pass-through path.
"""
import csv
import tempfile
import unittest
from pathlib import Path

from yotpo_adapter import REQUIRED_COLUMNS, load_rows, assert_ok, land

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE = FIXTURES / "yotpo_export_sample.csv"


class LoadRowsTests(unittest.TestCase):
    def test_loads_all_rows_with_required_columns_present(self):
        rows = load_rows(SAMPLE)
        self.assertEqual(len(rows), 5)
        for row in rows:
            for col in REQUIRED_COLUMNS:
                self.assertIn(col, row)

    def test_strips_whitespace_from_header_and_values(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="") as f:
            f.write(" ID , Title ,Content,Score,Created At,Market,Deleted,Escalated,"
                    "Product SKU,Product Title,Product Type\n")
            f.write("1, Padded Title ,Fine,5,1-Jan-2026,UK,FALSE,FALSE,SKU-1,Title,Type\n")
            path = f.name
        rows = load_rows(path)
        self.assertIn("ID", rows[0])
        self.assertEqual(rows[0]["Title"], "Padded Title")

    def test_missing_required_column_aborts(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="") as f:
            f.write("ID,Title,Content,Score,Created At\n")
            f.write("1,t,c,5,1-Jan-2026\n")
            path = f.name
        with self.assertRaises(SystemExit):
            load_rows(path)


class AssertOkTests(unittest.TestCase):
    def test_passes_on_the_real_sample_which_has_deleted_and_escalated_rows(self):
        rows = load_rows(SAMPLE)
        assert_ok(rows, allow_empty_flags=False)  # must not raise

    def test_aborts_on_empty_export(self):
        with self.assertRaises(SystemExit):
            assert_ok([], allow_empty_flags=False)

    def test_aborts_when_no_deleted_or_escalated_present(self):
        clean_rows = [r for r in load_rows(SAMPLE)
                      if r["Deleted"] == "FALSE" and r["Escalated"] == "FALSE"]
        self.assertTrue(clean_rows)
        with self.assertRaises(SystemExit):
            assert_ok(clean_rows, allow_empty_flags=False)

    def test_allow_empty_flags_overrides_the_guard(self):
        clean_rows = [r for r in load_rows(SAMPLE)
                      if r["Deleted"] == "FALSE" and r["Escalated"] == "FALSE"]
        assert_ok(clean_rows, allow_empty_flags=True)  # must not raise


class LandTests(unittest.TestCase):
    def test_lands_sorted_by_id_with_required_columns_first(self):
        rows = load_rows(SAMPLE)
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "reviews.csv"
            land(rows, str(out))
            with open(out, newline="", encoding="utf-8") as f:
                landed = list(csv.DictReader(f))
        self.assertEqual(len(landed), len(rows))
        ids = [r["ID"] for r in landed]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(list(landed[0].keys())[:len(REQUIRED_COLUMNS)], REQUIRED_COLUMNS)

    def test_extra_columns_pass_through(self):
        rows = load_rows(SAMPLE)
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "reviews.csv"
            land(rows, str(out))
            with open(out, newline="", encoding="utf-8") as f:
                landed = list(csv.DictReader(f))
        # e.g. "CORE?" is read by review_feedback.py but not in REQUIRED_COLUMNS
        self.assertIn("CORE?", landed[0])


if __name__ == "__main__":
    unittest.main()
