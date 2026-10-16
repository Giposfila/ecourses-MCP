import json
import pathlib
import sys
import unittest
from datetime import datetime
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import requests

from sfu_client import SFUClient, SFUError
from sfu_parsers import normalize_course, parse_courses_ajax_response

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"


def load_json(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def make_client() -> SFUClient:
    client = SFUClient(username="user", password="secret", min_interval=0)
    client._logged_in = True
    client._sesskey = "test-sesskey"
    return client


class ParseCoursesTest(unittest.TestCase):
    def test_parse_and_normalize(self):
        payload = load_json("courses_page1.json")
        courses, error = parse_courses_ajax_response(payload[0])
        self.assertIsNone(error)
        self.assertEqual(len(courses), 9)
        normalized = normalize_course(courses[0])
        self.assertEqual(normalized["id"], 39594)
        self.assertEqual(normalized["name"], "Интернет предпринимательство. 2.0")
        self.assertEqual(normalized["shortname"], "ИП2.0")
        self.assertTrue(normalized["has_progress"])

    def test_error_response(self):
        courses, error = parse_courses_ajax_response(
            {"error": True, "errorcode": "x", "exception": "boom"}
        )
        self.assertEqual(courses, [])
        self.assertEqual(error["exception"], "boom")


class CoursesPaginationTest(unittest.TestCase):
    def _course(self, cid, name):
        return {"id": cid, "fullname": name, "shortname": name, "viewurl": f"/c/{cid}"}

    def test_pagination_dedup_and_stop(self):
        client = make_client()
        c1, c2, c3, c4 = (
            self._course(1, "A"),
            self._course(2, "B"),
            self._course(3, "C"),
            self._course(4, "D"),
        )
        pages = [
            {"error": False, "data": {"courses": [c1, c2], "nextoffset": 2}},
            {"error": False, "data": {"courses": [c2, c3], "nextoffset": 4}},
            {"error": False, "data": {"courses": [c4], "nextoffset": 5}},
        ]

        def fake_post_ajax(methodname, payload):
            return [pages.pop(0)]

        client._post_ajax = fake_post_ajax
        with mock.patch("sfu_client.COURSES_PAGE_SIZE", 2):
            courses = client.get_courses()
        self.assertEqual([c["id"] for c in courses], [1, 2, 3, 4])

    def test_repeated_nextoffset_does_not_loop(self):
        client = make_client()
        page = {"error": False, "data": {"courses": [self._course(1, "A"), self._course(2, "B")], "nextoffset": 2}}
        calls = {"n": 0}

        def fake_post_ajax(methodname, payload):
            calls["n"] += 1
            return [page]

        client._post_ajax = fake_post_ajax
        with mock.patch("sfu_client.COURSES_PAGE_SIZE", 2):
            courses = client.get_courses()
        self.assertEqual([c["id"] for c in courses], [1, 2])
        self.assertEqual(calls["n"], 2)

    def test_error_page_raises(self):
        client = make_client()
        client._post_ajax = lambda methodname, payload: [
            {"error": True, "errorcode": "servicerequireslogin", "exception": "nope"}
        ]
        with self.assertRaises(SFUError):
            client.get_courses(use_cache=False)


class CourseIdValidationTest(unittest.TestCase):
    def test_invalid_ids(self):
        client = make_client()
        for bad in ("abc", None, 0, -5, True):
            with self.assertRaises(ValueError):
                client.get_grades(bad)


class DeadlinesTest(unittest.TestCase):
    def _client(self):
        client = make_client()
        client.get_courses = lambda use_cache=True: [
            {"id": 1, "name": "Курс A"},
            {"id": 2, "name": "Курс B"},
        ]

        def fake_assignments(course_id, use_cache=True):
            if course_id == 2:
                raise SFUError("таблица заданий не найдена")
            return [
                {
                    "name": "soon",
                    "module_id": 10,
                    "url": "/u",
                    "due_date_raw": "20 октября 2026, 23:59",
                    "due_at": "2026-10-20T23:59",
                    "due_warnings": [],
                    "submission_status": "unknown",
                    "grade_raw": None,
                },
                {
                    "name": "already submitted",
                    "module_id": 11,
                    "url": "/u",
                    "due_date_raw": "18 октября 2026, 23:59",
                    "due_at": "2026-10-18T23:59",
                    "due_warnings": [],
                    "submission_status": "submitted",
                    "grade_raw": None,
                },
                {
                    "name": "no date",
                    "module_id": None,
                    "url": None,
                    "due_date_raw": "по договорённости",
                    "due_at": None,
                    "due_warnings": ["due_date_unrecognized"],
                    "submission_status": "unknown",
                    "grade_raw": None,
                },
                {
                    "name": "too far",
                    "module_id": 12,
                    "url": "/u",
                    "due_date_raw": "1 января 2027",
                    "due_at": "2027-01-01T00:00",
                    "due_warnings": ["due_time_missing"],
                    "submission_status": "unknown",
                    "grade_raw": None,
                },
            ]

        client.get_assignments = fake_assignments
        return client

    def test_isolation_and_filtering(self):
        report = self._client().get_upcoming_deadlines(7, now=datetime(2026, 10, 17, 12, 0))
        self.assertEqual([item["name"] for item in report["items"]], ["soon"])
        self.assertEqual(len(report["undated"]), 1)
        self.assertEqual(len(report["course_errors"]), 1)
        self.assertEqual(report["course_errors"][0]["course_id"], 2)
        self.assertEqual(report["skipped_submitted"], 1)

    def test_include_submitted(self):
        report = self._client().get_upcoming_deadlines(
            7, include_submitted=True, now=datetime(2026, 10, 17, 12, 0)
        )
        names = sorted(item["name"] for item in report["items"])
        self.assertEqual(names, ["already submitted", "soon"])
        self.assertEqual(report["skipped_submitted"], 0)

    def test_days_clamped(self):
        report = self._client().get_upcoming_deadlines(1000, now=datetime(2026, 10, 17, 12, 0))
        self.assertEqual(report["days"], 365)


class ErrorSanitizationTest(unittest.TestCase):
    """Сообщения об ошибках не должны раскрывать query-строку с секретами."""

    def test_network_error_hides_secret(self):
        client = make_client()
        client.session = mock.Mock()
        client.session.request.side_effect = requests.exceptions.ConnectionError(
            "Max retries exceeded with url: "
            "/lib/ajax/service.php?sesskey=SECRET123&info=core_course"
        )
        with self.assertRaises(SFUError) as ctx:
            client._network("GET", "/my/")
        message = str(ctx.exception)
        self.assertNotIn("SECRET123", message)
        self.assertNotIn("sesskey", message)
        self.assertIn("/my/", message)

    def test_http_error_hides_query(self):
        client = make_client()
        fake = mock.Mock(
            status_code=500,
            url="https://e.sfu-kras.ru/lib/ajax/service.php?sesskey=SECRET123&info=x",
            history=[],
        )
        client.session = mock.Mock()
        client.session.request.return_value = fake
        with self.assertRaises(SFUError) as ctx:
            client._network("GET", "/lib/ajax/service.php", params={"sesskey": "SECRET123"})
        message = str(ctx.exception)
        self.assertNotIn("SECRET123", message)
        self.assertIn("500", message)


if __name__ == "__main__":
    unittest.main()
