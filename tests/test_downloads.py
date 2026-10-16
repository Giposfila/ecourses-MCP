import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from sfu_client import (
    SFUClient,
    SFUError,
    download_dir,
    download_max_bytes,
    safe_filename,
)
from sfu_parsers import classify_attachment

MATERIAL_URL = (
    "https://e.sfu-kras.ru/pluginfile.php/1431179/mod_assign/introattachment/0/"
    "lab_rab8.pdf?forcedownload=1"
)
RESOURCE_URL = "https://e.sfu-kras.ru/pluginfile.php/1/mod_resource/content/0/lecture.pdf"
SUBMISSION_URL = (
    "https://e.sfu-kras.ru/pluginfile.php/1431179/assignsubmission_file/"
    "submission_files/13537065/otchet.xlsx?forcedownload=1"
)
FEEDBACK_URL = "https://e.sfu-kras.ru/pluginfile.php/1/feedback_files/2/review.pdf"
FOREIGN_URL = "https://example.com/pluginfile.php/1/mod_resource/content/0/x.pdf"


class FakeResponse:
    def __init__(
        self,
        body: bytes = b"PDF-DATA",
        *,
        content_type: str = "application/pdf",
        url: str = MATERIAL_URL,
        content_length: int | None = None,
    ) -> None:
        self._body = body
        self.status_code = 200
        self.url = url
        self.history: list = []
        self.headers = {"Content-Type": content_type}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)
        self.closed = False

    @property
    def text(self) -> str:
        return self._body.decode("utf-8", "replace")

    def iter_content(self, chunk_size: int = 65536):
        for start in range(0, len(self._body), chunk_size):
            yield self._body[start : start + chunk_size]

    def close(self) -> None:
        self.closed = True


def make_client(response: FakeResponse) -> SFUClient:
    client = SFUClient(username="user", password="secret", min_interval=0)
    client._logged_in = True
    client.session = mock.Mock()
    client.session.request.return_value = response
    return client


def with_details(client: SFUClient, attachments) -> SFUClient:
    client.get_assignment_details = lambda module_id: {
        "module_id": module_id,
        "title": "Транспортная логистика",
        "attachments": attachments,
        "warnings": [],
    }
    return client


MATERIALS = [
    {"name": "lab_rab8.pdf", "url": MATERIAL_URL, "kind": "material"},
    {"name": "otchet.xlsx", "url": SUBMISSION_URL, "kind": "submission"},
]


class ClassifyAttachmentTest(unittest.TestCase):
    def test_materials(self):
        self.assertEqual(classify_attachment(MATERIAL_URL), "material")
        self.assertEqual(classify_attachment(RESOURCE_URL), "material")

    def test_submissions(self):
        self.assertEqual(classify_attachment(SUBMISSION_URL), "submission")
        self.assertEqual(classify_attachment(FEEDBACK_URL), "submission")

    def test_unknown_and_empty(self):
        self.assertEqual(classify_attachment("https://e.sfu-kras.ru/pluginfile.php/1/x/y"), "other")
        self.assertEqual(classify_attachment(None), "other")


class SafeFilenameTest(unittest.TestCase):
    def test_strips_path_and_unescapes(self):
        self.assertEqual(safe_filename("/a/b/lab%20rab8.pdf"), "lab rab8.pdf")

    def test_strips_traversal(self):
        self.assertEqual(safe_filename("../../etc/passwd"), "passwd")
        self.assertEqual(safe_filename(".."), "material")

    def test_replaces_forbidden_chars(self):
        self.assertEqual(safe_filename('a<b>c:d"e|f?g*h.pdf'), "a_b_c_d_e_f_g_h.pdf")

    def test_long_name_keeps_extension(self):
        name = safe_filename("x" * 300 + ".pdf")
        self.assertLessEqual(len(name), 120)
        self.assertTrue(name.endswith(".pdf"))

    def test_empty_falls_back(self):
        self.assertEqual(safe_filename("", fallback="file"), "file")


class DownloadDirConfigTest(unittest.TestCase):
    def test_env_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"SFU_DOWNLOAD_DIR": tmp}):
                self.assertEqual(str(download_dir()), str(pathlib.Path(tmp).resolve()))

    def test_max_bytes_env(self):
        with mock.patch.dict(os.environ, {"SFU_DOWNLOAD_MAX_MB": "3"}):
            self.assertEqual(download_max_bytes(), 3 * 1024 * 1024)
        with mock.patch.dict(os.environ, {"SFU_DOWNLOAD_MAX_MB": "мусор"}):
            self.assertEqual(download_max_bytes(), 25 * 1024 * 1024)


class ListMaterialsTest(unittest.TestCase):
    def test_only_materials_returned(self):
        client = with_details(make_client(FakeResponse()), MATERIALS)
        listing = client.list_assignment_materials(709030)
        self.assertEqual([m["name"] for m in listing["materials"]], ["lab_rab8.pdf"])
        self.assertEqual(listing["personal_files_count"], 1)
        self.assertEqual(listing["title"], "Транспортная логистика")

    def test_invalid_module_id(self):
        client = with_details(make_client(FakeResponse()), MATERIALS)
        for bad in (0, -1, "abc", None, True):
            with self.assertRaises(ValueError):
                client.list_assignment_materials(bad)


class DownloadMaterialsTest(unittest.TestCase):
    def _download(self, response: FakeResponse, attachments=MATERIALS, **kwargs):
        with tempfile.TemporaryDirectory() as tmp:
            client = with_details(make_client(response), attachments)
            report = client.download_assignment_materials(709030, dest_dir=tmp, **kwargs)
            files = sorted(p.name for p in pathlib.Path(tmp).iterdir())
            contents = {
                p.name: p.read_bytes() for p in pathlib.Path(tmp).iterdir() if p.is_file()
            }
            return report, files, contents

    def test_downloads_material(self):
        report, files, contents = self._download(
            FakeResponse(b"PDF-DATA"), MATERIALS
        )
        self.assertEqual(files, ["lab_rab8.pdf"])
        self.assertEqual(contents["lab_rab8.pdf"], b"PDF-DATA")
        self.assertEqual(report["downloaded"][0]["bytes"], 8)
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["skipped"], [])

    def test_submission_is_never_downloaded(self):
        report, files, _ = self._download(FakeResponse(), MATERIALS, names=["otchet.xlsx"])
        self.assertEqual(files, [])
        self.assertEqual(report["downloaded"], [])
        self.assertEqual(report["errors"], [])

    def test_existing_file_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = pathlib.Path(tmp) / "lab_rab8.pdf"
            target.write_bytes(b"OLD")
            client = with_details(make_client(FakeResponse(b"NEW")), MATERIALS)
            report = client.download_assignment_materials(709030, dest_dir=tmp)
            self.assertEqual(target.read_bytes(), b"OLD")
            self.assertEqual(report["skipped"][0]["skipped"], "already_exists")
            client.session.request.assert_not_called()

    def test_overwrite_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = pathlib.Path(tmp) / "lab_rab8.pdf"
            target.write_bytes(b"OLD")
            client = with_details(make_client(FakeResponse(b"NEW")), MATERIALS)
            report = client.download_assignment_materials(709030, dest_dir=tmp, overwrite=True)
            self.assertEqual(target.read_bytes(), b"NEW")
            self.assertEqual(len(report["downloaded"]), 1)

    def test_name_filter_without_match_downloads_nothing(self):
        report, files, _ = self._download(FakeResponse(), MATERIALS, names=["нет такого"])
        self.assertEqual(files, [])
        self.assertEqual(report["materials_total"], 1)

    def test_content_length_over_limit_is_error(self):
        response = FakeResponse(b"x", content_length=10 * 1024 * 1024)
        report, files, _ = self._download(response, MATERIALS, max_bytes=1024)
        self.assertEqual(files, [])
        self.assertEqual(len(report["errors"]), 1)
        self.assertIn("лимит", report["errors"][0]["error"])

    def test_stream_over_limit_leaves_no_partial_file(self):
        response = FakeResponse(b"y" * 5000)
        report, files, _ = self._download(response, MATERIALS, max_bytes=1024)
        self.assertEqual(files, [])
        self.assertEqual(len(report["errors"]), 1)

    def test_html_instead_of_file_is_error(self):
        response = FakeResponse(b"<html>nope</html>", content_type="text/html; charset=utf-8")
        report, files, _ = self._download(response, MATERIALS)
        self.assertEqual(files, [])
        self.assertIn("HTML", report["errors"][0]["error"])

    def test_personal_files_never_reach_disk_even_if_listed(self):
        attachments = [
            {"name": "otchet.xlsx", "url": SUBMISSION_URL, "kind": "material"},  # подделка
        ]
        client = with_details(make_client(FakeResponse()), attachments)
        with tempfile.TemporaryDirectory() as tmp:
            report = client.download_assignment_materials(709030, dest_dir=tmp)
            self.assertEqual(list(pathlib.Path(tmp).iterdir()), [])
            self.assertEqual(len(report["errors"]), 1)
            client.session.request.assert_not_called()

    def test_foreign_host_rejected(self):
        attachments = [{"name": "x.pdf", "url": FOREIGN_URL, "kind": "material"}]
        client = with_details(make_client(FakeResponse()), attachments)
        with tempfile.TemporaryDirectory() as tmp:
            report = client.download_assignment_materials(709030, dest_dir=tmp)
            self.assertEqual(list(pathlib.Path(tmp).iterdir()), [])
            self.assertIn("посторонний хост", report["errors"][0]["error"])
            client.session.request.assert_not_called()

    def test_direct_call_rejects_non_material(self):
        client = make_client(FakeResponse())
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SFUError):
                client._download_file(
                    SUBMISSION_URL,
                    pathlib.Path(tmp),
                    overwrite=False,
                    max_bytes=1024,
                )


if __name__ == "__main__":
    unittest.main()
