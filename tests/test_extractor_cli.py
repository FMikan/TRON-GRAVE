import io
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import grave_extractor
from extractor import image_processor
from extractor.csv_writer import CSV_COLUMNS, append_rows, read_csv, read_processed
from tests.helpers import FakeClient, answer, api_error, jpeg_bytes, message, record


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


class FatalErrorTests(CliCase):
    def test_a_billing_error_stops_the_run_with_a_tag(self):
        for name in ("p_1_x.jpg", "p_2_x.jpg"):
            self.add_image(name)
        code, _, err, client = self.run_cli(api_error(402))
        self.assertEqual((code, len(client.calls)), (1, 1))
        self.assertIn("error: [api-402]", err)

    def test_three_api_failures_in_a_row_stop_the_run(self):
        for i in range(5):
            self.add_image(f"p_{i}_x.jpg")
        code, _, err, client = self.run_cli(api_error(400), api_error(400), api_error(400))
        self.assertEqual((code, len(client.calls)), (1, 3))
        self.assertIn("error: [api-down]", err)
        self.assertFalse((self.out / "byhand").exists())   # API failures are not review cases

    def test_an_answer_resets_the_failure_count(self):
        for i in range(4):
            self.add_image(f"p_{i}_x.jpg")
        code, _, _, client = self.run_cli(api_error(400), api_error(400), message(answer(record())), api_error(400))
        self.assertEqual((code, len(client.calls)), (2, 4))


class RobustnessTests(CliCase):
    def test_an_unexpected_error_on_one_photo_does_not_end_the_run(self):
        self.add_image("p_1_x.jpg")
        self.add_image("p_2_x.jpg")
        real_prepare = image_processor.prepare_image
        calls = []

        def flaky(raw):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("boom")
            return real_prepare(raw)

        with mock.patch.object(image_processor, "prepare_image", side_effect=flaky):
            code, _, _, client = self.run_cli(message(answer(record())))
        self.assertEqual((code, len(client.calls)), (2, 1))
        self.assertEqual([r[5] for r in self.rows()], ["neočekivana greška", ""])

    def test_a_locked_csv_stops_the_run_before_paying(self):
        self.add_image("p_1_x.jpg")
        with mock.patch.object(grave_extractor, "check_writable", side_effect=PermissionError("locked")):
            code, _, err, client = self.run_cli(message(answer(record())))
        self.assertEqual((code, client.calls), (1, []))
        self.assertIn("[csv-locked]", err)

    def test_a_lock_that_appears_mid_run_is_waited_out(self):
        self.add_image("p_1_x.jpg")
        real_append = grave_extractor.append_rows
        calls = []

        def flaky(path, rows):
            calls.append(1)
            if len(calls) == 1:
                raise PermissionError("locked")
            real_append(path, rows)

        with mock.patch.object(grave_extractor, "append_rows", side_effect=flaky):
            code, _, err, _ = self.run_cli(message(answer(record())))
        self.assertEqual(code, 0)
        self.assertIn("locked", err)
        self.assertEqual(len(self.rows()), 1)

    def test_a_lock_that_never_lifts_stops_the_run_after_30_seconds(self):
        self.add_image("p_1_x.jpg")
        with mock.patch.object(grave_extractor, "append_rows", side_effect=PermissionError("locked")):
            code, _, err, _ = self.run_cli(message(answer(record())))
        self.assertEqual(code, 1)
        self.assertIn("[csv-locked]", err)
        self.assertEqual(time.sleep.call_args_list, [mock.call(2)] * 15)

    def test_a_failed_byhand_copy_is_only_a_warning(self):
        self.add_image("p_1_x.jpg")
        with mock.patch.object(grave_extractor, "copy_to_byhand", side_effect=OSError("disk full")):
            code, _, err, _ = self.run_cli(message(answer(record(name=None))))
        self.assertEqual(code, 2)
        self.assertIn("could not copy p_1_x.jpg", err)
        self.assertIn("p_1_x.jpg", read_processed(self.out))


class ResumeTests(CliCase):
    def test_resume_retries_only_api_failures_and_keeps_one_row_per_photo(self):
        self.add_image("p_1_x.jpg")
        self.add_image("p_2_x.jpg")
        self.run_cli(message(answer(record())), api_error(400))
        code, _, _, client = self.run_cli(message(answer(record(name="Ana"))), args=("--resume",))
        self.assertEqual((code, len(client.calls)), (0, 1))
        self.assertEqual(sorted((r[6], r[1]) for r in self.rows()),
                         [("p_1_x.jpg", "Ivan"), ("p_2_x.jpg", "Ana")])

    def test_answered_photos_are_never_sent_again(self):
        self.add_image("p_1_x.jpg")
        self.add_image("p_2_x.jpg")
        self.run_cli(message(answer(error="sve nečitko")), message(answer(record(name=None, surname=None))))
        code, _, _, client = self.run_cli(args=("--resume",))
        self.assertEqual(client.calls, [])
        self.assertEqual(len(self.rows()), 2)

    def test_a_row_written_but_not_marked_is_rerun_once(self):
        self.add_image("p_1_x.jpg")
        self.run_cli(message(answer(record())))
        # killed after writing p_2's row, before marking it processed
        append_rows(self.out / "output.csv", [["2", "Kill", "Ed", "", "", "", "p_2_x.jpg"]])
        self.add_image("p_2_x.jpg")
        code, _, _, client = self.run_cli(message(answer(record(name="Ana"))), args=("--resume",))
        self.assertEqual(len(client.calls), 1)
        self.assertEqual([r[1] for r in self.rows() if r[6] == "p_2_x.jpg"], ["Ana"])

    def test_a_failure_row_of_a_photo_missing_from_the_resumed_input_survives(self):
        self.add_image("p_1_x.jpg")
        gone = self.add_image("p_2_x.jpg")
        self.run_cli(message(answer(record())), api_error(400))
        gone.unlink()                        # the resumed input is another folder: p_2 is not re-run
        self.add_image("p_3_x.jpg")
        code, _, _, client = self.run_cli(message(answer(record(name="Ana"))), args=("--resume",))
        self.assertEqual((code, len(client.calls)), (0, 1))
        self.assertEqual(sorted((r[6], r[1], r[5]) for r in self.rows()),
                         [("p_1_x.jpg", "Ivan", ""), ("p_2_x.jpg", "", "greška API-ja"), ("p_3_x.jpg", "Ana", "")])

    def test_a_row_with_a_blank_file_cell_survives_resume(self):
        self.add_image("p_1_x.jpg")
        self.add_image("p_2_x.jpg")
        self.run_cli(message(answer(record())), api_error(400))
        typed_in_excel = ["99", "Hand", "Added", "", "", "typed in Excel", ""]
        append_rows(self.out / "output.csv", [typed_in_excel])
        code, _, _, client = self.run_cli(message(answer(record(name="Ana"))), args=("--resume",))
        self.assertEqual((code, len(client.calls), len(self.rows())), (0, 1, 3))
        self.assertIn(typed_in_excel, self.rows())

    def test_a_row_cut_short_survives_resume(self):
        self.add_image("p_1_x.jpg")
        self.add_image("p_2_x.jpg")
        self.run_cli(message(answer(record())), api_error(400))
        append_rows(self.out / "output.csv", [["5", "torn"]])     # too short to say whose row it is
        code, _, _, client = self.run_cli(message(answer(record(name="Ana"))), args=("--resume",))
        self.assertEqual((code, len(client.calls), len(self.rows())), (0, 1, 3))
        self.assertIn(["5", "torn"], self.rows())

    def test_resume_puts_the_header_back_into_a_zero_byte_output_csv(self):
        self.add_image("p_1_x.jpg")
        self.out.mkdir()
        (self.out / "output.csv").write_bytes(b"")
        (self.out / ".processed").write_text("", encoding="utf-8")
        code, _, _, client = self.run_cli(message(answer(record())), args=("--resume",))
        self.assertEqual((code, len(client.calls)), (0, 1))
        header, rows = read_csv(self.out / "output.csv")
        self.assertEqual((header, len(rows)), (CSV_COLUMNS, 1))

    def test_resume_refuses_folders_it_cannot_resume_safely(self):
        self.add_image("p_1_x.jpg")
        self.out.mkdir()
        cases = {
            "old-format": ("ID,Name,Surname,Year of Birth,Year of Death,Notes\n1,A,B,,,\n", None),
            "no-processed": (",".join(CSV_COLUMNS) + "\n1,A,B,,,,p_1_x.jpg\n", None),
            "missing-csv": (None, "p_1_x.jpg\n"),
        }
        for name, (content, processed) in cases.items():
            with self.subTest(name):
                for f in ("output.csv", ".processed"):
                    (self.out / f).unlink(missing_ok=True)
                if content is not None:
                    (self.out / "output.csv").write_text(content, encoding="utf-8-sig")
                if processed is not None:
                    (self.out / ".processed").write_text(processed, encoding="utf-8")
                code, _, err, client = self.run_cli(args=("--resume",))
                self.assertEqual((code, client.calls), (1, []))
                self.assertIn("[resume-refused]", err)

    def test_a_fresh_run_clears_old_byhand_copies(self):
        self.add_image("p_1_x.jpg")
        self.run_cli(message(answer(record(name=None))))
        self.assertTrue((self.out / "byhand" / "p_1_x.jpg").exists())
        # An API failure neither copies nor removes, so only the fresh-run clear can delete the copy.
        self.run_cli(api_error(400))
        self.assertFalse((self.out / "byhand" / "p_1_x.jpg").exists())

    def test_a_resumed_photo_that_now_reads_fine_leaves_byhand(self):
        img = self.add_image("p_1_x.jpg", b"not a jpeg yet")
        self.run_cli()                       # unreadable: copied for review, not marked done
        self.assertTrue((self.out / "byhand" / "p_1_x.jpg").exists())
        img.write_bytes(jpeg_bytes())
        code, _, _, client = self.run_cli(message(answer(record())), args=("--resume",))
        self.assertEqual((code, len(client.calls), len(self.rows())), (0, 1, 1))
        self.assertFalse((self.out / "byhand" / "p_1_x.jpg").exists())

    def test_dry_run_with_resume_lists_only_what_would_run(self):
        self.add_image("p_1_x.jpg")
        self.add_image("p_2_x.jpg")
        self.run_cli(message(answer(record())), api_error(400))
        code, out, _, _ = self.run_cli(args=("--resume", "--dry-run"))
        self.assertEqual(code, 0)
        self.assertNotIn("p_1_x.jpg", out)
        self.assertIn("p_2_x.jpg", out)

    def test_non_ascii_names_survive_resume(self):
        self.add_image("ploča_12_Čakovec.jpg")
        self.add_image("ploča_13_Čakovec.jpg")
        self.run_cli(message(answer(record())), api_error(400))
        _, _, _, client = self.run_cli(message(answer(record(name="Ana"))), args=("--resume",))
        self.assertEqual((len(client.calls), len(self.rows())), (1, 2))

    def test_resume_with_nothing_left_finishes_cleanly(self):
        self.add_image("p_1_x.jpg")
        self.run_cli(message(answer(record())))
        code, out, _, client = self.run_cli(args=("--resume",))
        self.assertEqual((code, client.calls), (0, []))
        self.assertIn("Done. 0 images processed", out)

    def test_the_review_count_is_read_from_byhand_on_disk(self):
        self.add_image("p_1_x.jpg")
        self.run_cli(message(answer(record(name=None))))
        code, out, _, client = self.run_cli(args=("--resume",))   # nothing left to run
        self.assertEqual((code, client.calls), (0, []))
        self.assertIn("Review:", out)
        self.assertIn("(1 images)", out)


class InputTests(CliCase):
    def test_skipped_heic_photos_are_reported_and_dotfiles_ignored(self):
        self.add_image("p_1_x.jpg")
        self.add_image("p_2_x.HEIC", b"heic")
        self.add_image("._p_3_x.jpg", b"appledouble")
        code, _, err, client = self.run_cli(message(answer(record())))
        self.assertEqual((code, len(client.calls)), (0, 1))
        self.assertIn("skipping 1 .heic/.heif file(s)", err)

    def test_the_outputs_byhand_folder_is_refused_as_input(self):
        self.inp = self.out / "byhand"
        self.inp.mkdir(parents=True)
        self.add_image("p_1_x.jpg")
        code, _, err, client = self.run_cli()
        self.assertEqual((code, client.calls), (1, []))
        self.assertIn("[input-is-byhand]", err)

    def test_an_unpriced_model_gets_a_cost_warning(self):
        self.add_image("p_1_x.jpg")
        _, _, err, _ = self.run_cli(message(answer(record())), args=("--model", "claude-haiku-9"))
        self.assertIn("no price table entry for claude-haiku-9", err)

    def test_a_priced_model_and_supported_photos_print_no_warnings(self):
        self.add_image("p_1_x.jpg")
        _, _, err, _ = self.run_cli(message(answer(record())), args=("--model", "claude-sonnet-5-5"))
        self.assertEqual(err, "")

    def test_byhand_reached_by_another_path_is_refused_before_anything_is_touched(self):
        (self.out / "elsewhere").mkdir(parents=True)
        self.inp = self.out / "byhand"
        self.inp.mkdir()
        photo = self.add_image("p_1_x.jpg")
        old_csv = self.out / "output.csv"
        old_csv.write_text("old run", encoding="utf-8")
        self.inp = self.out / "elsewhere" / ".." / "byhand"
        code, _, err, client = self.run_cli()
        self.assertEqual((code, client.calls), (1, []))
        self.assertIn("[input-is-byhand]", err)
        self.assertTrue(photo.exists())                                   # not cleared
        self.assertEqual(old_csv.read_text(encoding="utf-8"), "old run")  # not replaced

    def test_the_byhand_refusal_comes_before_the_resume_checks(self):
        self.inp = self.out / "byhand"
        self.inp.mkdir(parents=True)
        self.add_image("p_1_x.jpg")
        (self.out / "output.csv").write_text("old run", encoding="utf-8")   # --resume alone refuses this
        code, _, err, client = self.run_cli(args=("--resume",))
        self.assertEqual((code, client.calls), (1, []))
        self.assertIn("[input-is-byhand]", err)


class VerboseFormatTests(CliCase):
    def test_each_photo_gets_a_start_line_and_a_result_line(self):
        self.add_image("p_1_x.jpg")
        self.add_image("p_2_x.jpg")
        _, out, _, _ = self.run_cli(message(answer(record())), message(answer(record(name=None))))
        lines = out.splitlines()
        self.assertEqual(lines[0], "[1/2] Processing p_1_x.jpg ...")
        self.assertRegex(lines[1], r"^\[1/2\] OK: p_1_x\.jpg \(1 record\) — \$\d+\.\d{4} \(total: \$\d+\.\d{2}\)$")
        self.assertEqual(lines[2], "[2/2] Processing p_2_x.jpg ...")
        self.assertTrue(lines[3].startswith("[2/2] PARTIAL: p_2_x.jpg (Name or surname could not be read)"))
