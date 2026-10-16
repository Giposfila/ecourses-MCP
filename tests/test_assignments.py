import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from datetime import datetime

from sfu_parsers import (
    ParseError,
    map_submission_status,
    parse_assignment_details,
    parse_assignments,
)

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
NOW = datetime(2026, 10, 17, 12, 0)


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class ParseAssignmentsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = parse_assignments(load("assignments_41121.html"), now=NOW)
        cls.by_name = {row["name"]: row for row in cls.rows}

    def test_row_count(self):
        self.assertEqual(len(self.rows), 5)

    def test_link_and_module_id(self):
        row = self.by_name["ЛР1. Изучение инкапсуляции протоколов"]
        self.assertEqual(row["module_id"], 2089577)
        self.assertTrue(row["url"].endswith("/mod/assign/view.php?id=2089577"))
        self.assertEqual(row["submission_status"], "submitted")
        self.assertEqual(row["section"], "Раздел 1. Введение")

    def test_datetime_attr_used(self):
        row = self.by_name["ЛР2. Базовая конфигурация сети"]
        self.assertEqual(row["due_at"], "2026-10-17T23:59")
        self.assertEqual(row["grade_numeric"], 8.0)

    def test_no_link_gives_null_module_id(self):
        row = self.by_name["ЛР3. NAT. Маршрутизация трафика в сети"]
        self.assertIsNone(row["module_id"])
        self.assertIsNone(row["url"])
        self.assertEqual(row["section"], "Раздел 2. Сети")

    def test_empty_status_is_unknown_not_not_submitted(self):
        row = self.by_name["ЛР3. NAT. Маршрутизация трафика в сети"]
        self.assertIsNone(row["submission_status_raw"])
        self.assertEqual(row["submission_status"], "unknown")

    def test_relative_year_resolved_by_weekday(self):
        row = self.by_name["ЛР4. Настройка общего доступа и групповой политики"]
        self.assertEqual(row["due_at"], "2026-11-22T23:57")

    def test_far_future_2028(self):
        row = self.by_name["ЛР8. HTTP и настройка web-сервера на Linux/Windows"]
        self.assertEqual(row["due_at"], "2028-01-03T23:40")

    def test_empty_table_returns_empty_list(self):
        self.assertEqual(parse_assignments(load("assignments_empty.html"), now=NOW), [])

    def test_missing_table_raises(self):
        with self.assertRaises(ParseError):
            parse_assignments("<html><body>нет таблицы</body></html>", now=NOW)


class SubmissionStatusTest(unittest.TestCase):
    def test_mapping(self):
        self.assertEqual(map_submission_status("Отправлено для оценивания"), "submitted")
        self.assertEqual(map_submission_status("Не отправлено"), "not_submitted")
        self.assertEqual(map_submission_status("Нет ответа"), "not_submitted")
        self.assertEqual(map_submission_status("Черновик (не отправлено)"), "not_submitted")
        self.assertEqual(map_submission_status(""), "unknown")
        self.assertEqual(map_submission_status(None), "unknown")
        self.assertEqual(map_submission_status("что-то иное"), "unknown")


class ParseAssignmentDetailsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.details = parse_assignment_details(
            load("assignment_details_2089577.html"), module_id=2089577, now=NOW
        )

    def test_title_and_description(self):
        self.assertEqual(
            self.details["title"], "ЛР1. Изучение инкапсуляции протоколов"
        )
        self.assertIn("инкапсуляцию протоколов", self.details["description"])

    def test_status_table_fields(self):
        self.assertEqual(self.details["due_at"], "2026-10-01T23:59")
        self.assertEqual(self.details["submission_status"], "submitted")
        self.assertEqual(self.details["grading_status_raw"], "Не оценено")
        self.assertEqual(self.details["last_modified_raw"], "24 сентября 2026, 18:53")

    def test_attachments(self):
        self.assertEqual(len(self.details["attachments"]), 1)
        self.assertEqual(self.details["attachments"][0]["name"], "readme.pdf")


if __name__ == "__main__":
    unittest.main()
