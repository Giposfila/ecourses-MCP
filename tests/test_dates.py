import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from datetime import datetime

from sfu_dates import parse_due_date

NOW = datetime(2026, 10, 17, 12, 0)


class ParseDueDateTest(unittest.TestCase):
    def test_datetime_attr_has_priority(self):
        result = parse_due_date(
            "17 октября 2026, 23:59",
            datetime_attr="2026-10-17T23:59:00+07:00",
            now=NOW,
        )
        self.assertEqual(result.due_at, "2026-10-17T23:59")
        self.assertEqual(result.warnings, [])

    def test_absolute_with_parenthesized_year(self):
        result = parse_due_date("четверг 1 октября, 23:59 (2026)", now=NOW)
        self.assertEqual(result.due_at, "2026-10-01T23:59")
        self.assertNotIn("weekday_mismatch", result.warnings)

    def test_absolute_year_2028(self):
        result = parse_due_date("понедельник 3 января 2028, 23:40", now=NOW)
        self.assertEqual(result.due_at, "2028-01-03T23:40")

    def test_relative_today(self):
        result = parse_due_date("Сегодня, 18:30", now=NOW)
        self.assertEqual(result.due_at, "2026-10-17T18:30")

    def test_relative_tomorrow(self):
        result = parse_due_date("Завтра, 10:00", now=NOW)
        self.assertEqual(result.due_at, "2026-10-18T10:00")

    def test_year_inferred_from_weekday(self):
        result = parse_due_date("суббота 31 октября, 22:11", now=NOW)
        self.assertEqual(result.due_at, "2026-10-31T22:11")
        self.assertIn("year_inferred_from_weekday", result.warnings)

    def test_year_ambiguous_without_weekday(self):
        result = parse_due_date("31 октября, 22:11", now=NOW)
        self.assertIsNone(result.due_at)
        self.assertIn("year_missing", result.warnings)

    def test_weekday_mismatch_is_flagged_but_parsed(self):
        result = parse_due_date("суббота 1 октября, 23:59 (2026)", now=NOW)
        self.assertEqual(result.due_at, "2026-10-01T23:59")
        self.assertIn("weekday_mismatch", result.warnings)

    def test_missing_time_is_flagged(self):
        result = parse_due_date("1 октября 2026", now=NOW)
        self.assertEqual(result.due_at, "2026-10-01T00:00")
        self.assertIn("due_time_missing", result.warnings)

    def test_empty_text(self):
        result = parse_due_date(None, now=NOW)
        self.assertIsNone(result.due_at)
        self.assertIn("due_date_empty", result.warnings)

    def test_unrecognized_text(self):
        result = parse_due_date("по договорённости", now=NOW)
        self.assertIsNone(result.due_at)
        self.assertIn("due_date_unrecognized", result.warnings)


if __name__ == "__main__":
    unittest.main()
