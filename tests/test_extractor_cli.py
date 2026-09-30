import io
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import grave_extractor
from extractor.csv_writer import read_csv
from tests.helpers import FakeClient, answer, jpeg_bytes, message, record


class CliCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.inp = self.tmp / "in"
        self.inp.mkdir()
        self.out = self.tmp / "out"
        for patcher in (mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}),
                        mock.patch("time.sleep"),
                        mock.patch.object(grave_extractor, "load_dotenv"),
                        mock.patch.object(grave_extractor.signal, "signal")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def add_image(self, name, data=None) -> Path:
        path = self.inp / name
        path.write_bytes(jpeg_bytes() if data is None else data)
        return path

    def run_cli(self, *outcomes, args=()):
        """grave_extractor.main() against a FakeClient -> (exit code, stdout, stderr, client)."""
        client = FakeClient(*outcomes)
        argv = ["grave_extractor", "--input", str(self.inp), "--output", str(self.out), "--verbose", *args]
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "argv", argv), \
                mock.patch.object(grave_extractor.anthropic, "Anthropic", return_value=client), \
                mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", err):
            try:
                code = grave_extractor.main()
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue(), client

    def rows(self) -> list[list[str]]:
        return read_csv(self.out / "output.csv")[1]


class IdTagTests(CliCase):
    def test_filename_id_tag_is_written_once_after_the_other_tags(self):
        self.add_image("img001.jpg")
        code, *_ = self.run_cli(message(answer(record(birth=None, birth_status="unreadable"))))
        self.assertEqual(code, 2)
        self.assertEqual(self.rows()[0][5], "god. rođenja nečitka; ID iz naziva")
