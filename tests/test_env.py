import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from sfu_env import load_dotenv


class LoadDotenvTest(unittest.TestCase):
    def setUp(self):
        self._saved = dict(os.environ)
        self.addCleanup(self._restore)

    def _restore(self):
        os.environ.clear()
        os.environ.update(self._saved)

    def _write(self, content: str) -> pathlib.Path:
        directory = tempfile.mkdtemp()
        path = pathlib.Path(directory) / ".env"
        path.write_text(content, encoding="utf-8")
        return path

    def test_basic_parsing(self):
        path = self._write(
            "# комментарий\n"
            'SFU_USERNAME="student1"\n'
            "SFU_PASSWORD=secret value\n"
            "export EXTRA='x'\n"
            "\n"
            "BROKEN_LINE\n"
        )
        os.environ.pop("SFU_USERNAME", None)
        os.environ.pop("SFU_PASSWORD", None)
        loaded = load_dotenv(path)
        self.assertEqual(os.environ["SFU_USERNAME"], "student1")
        self.assertEqual(os.environ["SFU_PASSWORD"], "secret value")
        self.assertEqual(os.environ["EXTRA"], "x")
        self.assertIn("SFU_USERNAME", loaded)

    def test_existing_environment_wins(self):
        path = self._write("SFU_USERNAME=from_file\n")
        os.environ["SFU_USERNAME"] = "from_shell"
        load_dotenv(path)
        self.assertEqual(os.environ["SFU_USERNAME"], "from_shell")

    def test_override(self):
        path = self._write("SFU_USERNAME=from_file\n")
        os.environ["SFU_USERNAME"] = "from_shell"
        load_dotenv(path, override=True)
        self.assertEqual(os.environ["SFU_USERNAME"], "from_file")

    def test_missing_file_is_noop(self):
        self.assertEqual(load_dotenv("/tmp/definitely-not-here.env"), {})


if __name__ == "__main__":
    unittest.main()
