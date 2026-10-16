import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from sfu_parsers import ParseError, parse_grades

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class ParseGradesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = parse_grades(load("grades_41121.html"))
        cls.by_name = {row["name"]: row for row in cls.rows}

    def test_row_count_excludes_headings(self):
        # 3 теста + 2 задания + 2 итога категорий + итоговая + вычисляемая
        self.assertEqual(len(self.rows), 9)

    def test_test_row_numeric_grade_and_range(self):
        row = self.by_name["Входной тест"]
        self.assertEqual(row["item_type"], "test")
        self.assertEqual(row["grade_raw"], "8,00")
        self.assertEqual(row["grade_numeric"], 8.0)
        self.assertEqual(row["range_raw"], "0–10")
        self.assertEqual(row["range_min"], 0.0)
        self.assertEqual(row["range_max"], 10.0)
        self.assertFalse(row["is_total"])

    def test_dash_grade_is_none_not_zero(self):
        row = self.by_name["Тест 3"]
        self.assertIsNone(row["grade_raw"])
        self.assertIsNone(row["grade_numeric"])
        self.assertIn("grade_missing", row["warnings"])

    def test_action_menu_noise_removed(self):
        row = self.by_name["Лабораторная 2"]
        self.assertEqual(row["grade_raw"], "8,00")
        self.assertEqual(row["grade_numeric"], 8.0)
        self.assertEqual(row["feedback"], "Хорошо")

    def test_category_totals_are_totals(self):
        row = self.by_name["Итог категории тестов"]
        self.assertEqual(row["item_type"], "category_total")
        self.assertTrue(row["is_total"])
        self.assertEqual(row["grade_numeric"], 28.0)

    def test_final_grade_keeps_text(self):
        row = self.by_name["Итоговая оценка"]
        self.assertEqual(row["item_type"], "final_grade")
        self.assertTrue(row["is_total"])
        self.assertEqual(row["grade_raw"], "не зачтено")
        self.assertIsNone(row["grade_numeric"])

    def test_computed_grade(self):
        row = self.by_name["Вычисляемая оценка"]
        self.assertEqual(row["item_type"], "computed_grade")
        self.assertEqual(row["grade_numeric"], 37.0)

    def test_categories_tracked(self):
        self.assertEqual(self.by_name["Входной тест"]["category"], "Тесты")
        self.assertEqual(self.by_name["Лабораторная 2"]["category"], "Лабораторные работы")

    def test_missing_table_raises(self):
        with self.assertRaises(ParseError):
            parse_grades("<html><body><p>нет таблицы</p></body></html>")


class GradesTotalsTest(unittest.TestCase):
    """Префиксы служебных строк Moodle не должны дублировать название."""

    @classmethod
    def setUpClass(cls):
        cls.by_name = {row["name"]: row for row in parse_grades(load("grades_totals.html"))}

    def test_category_total_name_cleaned(self):
        row = self.by_name["Итого в категории «Тесты»"]
        self.assertEqual(row["item_type"], "final_grade")
        self.assertTrue(row["is_total"])
        self.assertEqual(row["grade_numeric"], 28.0)

    def test_computed_grade_name_cleaned(self):
        row = self.by_name["Рекомендованная оценка"]
        self.assertEqual(row["item_type"], "computed_grade")
        self.assertEqual(row["grade_raw"], "не зачтено")

    def test_course_total_name_cleaned(self):
        row = self.by_name["Итоговая оценка за курс"]
        self.assertEqual(row["item_type"], "final_grade")
        self.assertEqual(row["grade_numeric"], 37.0)

    def test_raw_name_kept(self):
        row = self.by_name["Итого в категории «Тесты»"]
        self.assertEqual(row["raw_name"], "Итоговая оценка Итого в категории «Тесты»")


if __name__ == "__main__":
    unittest.main()
