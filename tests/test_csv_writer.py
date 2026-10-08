import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from extractor.csv_writer import (
    CSV_COLUMNS, append_rows, check_writable, csv_text, init_csv, init_processed,
    mark_processed, read_csv, read_processed, resume_problem, rewrite_rows,
)


class CsvCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.csv = self.tmp / "output.csv"


class CsvTests(CsvCase):
    def test_header_ends_with_the_file_column(self):
        init_csv(self.csv)
        self.assertEqual(read_csv(self.csv)[0],
                         ["ID", "Name", "Surname", "Year of Birth", "Year of Death", "Notes", "File"])

    def test_a_filename_that_is_not_utf8_neither_crashes_nor_breaks_resume(self):
        name = b"ploca_7_\xe8u\xe8erje.jpg".decode("utf-8", "surrogateescape")   # Linux, from a ZIP
        init_csv(self.csv)
        append_rows(self.csv, [["7", "Ivan", "Horvat", 1920, 1999, "", name]])
        self.assertEqual(read_csv(self.csv)[1][0][6], csv_text(name))
        mark_processed(self.tmp, Path(name))
        self.assertIn(name, read_processed(self.tmp))

    def test_a_file_saved_by_excel_in_the_windows_code_page_still_reads(self):
        self.csv.write_bytes("ID;Name\r\n1;Mišo\r\n".encode("cp1250"))
        header, rows = read_csv(self.csv)
        self.assertEqual((header, len(rows)), (["ID;Name"], 1))

    def test_missing_file_reads_as_none(self):
        self.assertEqual(read_csv(self.tmp / "none.csv"), (None, []))

    def test_rewrite_keeps_the_header(self):
        init_csv(self.csv)
        append_rows(self.csv, [["1", "A", "", "", "", "", "a.jpg"], ["2", "B", "", "", "", "", "b.jpg"]])
        rewrite_rows(self.csv, [["2", "B", "", "", "", "", "b.jpg"]])
        self.assertEqual(read_csv(self.csv), (CSV_COLUMNS, [["2", "B", "", "", "", "", "b.jpg"]]))

    @unittest.skipIf(os.name == "nt" or os.geteuid() == 0, "needs POSIX permissions as a normal user")
    def test_check_writable_raises_when_the_file_is_locked(self):
        init_csv(self.csv)
        os.chmod(self.csv, 0o444)
        with self.assertRaises(OSError):
            check_writable(self.csv)


class ResumeProblemTests(CsvCase):
    def test_an_empty_folder_is_fine(self):
        self.assertIsNone(resume_problem(self.tmp))

    def test_an_older_versions_csv_is_refused(self):
        self.csv.write_text("ID,Name,Surname,Year of Birth,Year of Death,Notes\n1,A,B,,,\n", encoding="utf-8-sig")
        self.assertEqual(resume_problem(self.tmp), "old-format")

    def test_rows_without_a_processed_file_are_refused(self):
        init_csv(self.csv)
        append_rows(self.csv, [["1", "A", "", "", "", "", "a.jpg"]])
        self.assertEqual(resume_problem(self.tmp), "no-processed")

    def test_an_empty_processed_file_is_normal(self):
        init_csv(self.csv)
        append_rows(self.csv, [["1", "", "", "", "", "greška API-ja", "a.jpg"]])
        init_processed(self.tmp)
        self.assertIsNone(resume_problem(self.tmp))

    def test_processed_without_the_csv_is_refused(self):
        (self.tmp / ".processed").write_text("a.jpg\n", encoding="utf-8")
        self.assertEqual(resume_problem(self.tmp), "missing-csv")

    def test_a_header_only_csv_with_a_processed_list_is_refused(self):
        init_csv(self.csv)
        (self.tmp / ".processed").write_text("a.jpg\n", encoding="utf-8")
        self.assertEqual(resume_problem(self.tmp), "missing-csv")

    def test_an_ansi_resave_with_the_right_header_is_refused(self):
        self.csv.write_bytes((",".join(CSV_COLUMNS) + "\r\n1,Mišo,Čupić,1920,1999,,p_1_x.jpg\r\n").encode("cp1250"))
        (self.tmp / ".processed").write_text("p_1_x.jpg\n", encoding="utf-8")
        self.assertEqual(resume_problem(self.tmp), "not-utf8")

    def test_an_unreadable_processed_list_is_refused_not_taken_for_empty(self):
        # a OneDrive file left in the cloud, a dropped network drive: read as empty, the resume
        # would drop every row and pay for every photo again
        init_csv(self.csv)
        append_rows(self.csv, [["1", "A", "", "", "", "", "a.jpg"]])
        (self.tmp / ".processed").write_text("a.jpg\n", encoding="utf-8")
        real_read_text = Path.read_text

        def offline(path, *args, **kwargs):
            if path.name == ".processed":
                raise OSError(22, "The cloud file provider is not running")
            return real_read_text(path, *args, **kwargs)

        with mock.patch.object(Path, "read_text", offline):
            self.assertEqual(resume_problem(self.tmp), "unreadable")
            with self.assertRaises(OSError):
                read_processed(self.tmp)
        self.assertEqual(read_processed(self.tmp / "nowhere"), set())     # no list yet: nothing done

    def test_a_utf8_bom_file_is_still_resumable(self):
        init_csv(self.csv)
        append_rows(self.csv, [["1", "Mišo", "Čupić", 1920, 1999, "", "p_1_x.jpg"]])
        init_processed(self.tmp)
        self.assertIsNone(resume_problem(self.tmp))
