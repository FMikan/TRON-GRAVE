import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

from extractor import image_processor as ip
from tests.helpers import FakeClient, MidStream, answer, api_error, jpeg_bytes, message, record

# Sonnet 5.5, Opus 5.5 and the Fable models decline a prompt that asks them to write out their
# reasoning; Sonnet 5, Opus 5 and the 4.x models keep the scratchpad. Any other id gets the lean request.
LEAN_MODELS = ("claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5", "claude-fable-5-1", "claude-future-9")
FULL_MODELS = ("claude-sonnet-5", "claude-opus-5", "claude-sonnet-4-6", "claude-opus-4-8")


class ProcessImageCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.img = self.tmp / "pokojnici-ploca_305_22-07-2026_11-29-39.jpg"
        self.img.write_bytes(jpeg_bytes())
        sleep = mock.patch("time.sleep")
        sleep.start()
        self.addCleanup(sleep.stop)

    def run_image(self, *outcomes, effort="high", model="claude-sonnet-5"):
        client = FakeClient(*outcomes)
        return ip.process_image(client, model, self.img, "305", effort), client


class RequestShapeTests(ProcessImageCase):
    def request_for(self, model):
        """The kwargs of the one streaming call process_image makes for `model`."""
        _, client = self.run_image(message(answer(record())), model=model)
        return client.calls[0]

    def test_uses_structured_output_not_a_forced_tool(self):
        _, client = self.run_image(message(answer(record())), model="claude-sonnet-5")
        kwargs = client.calls[0]
        self.assertNotIn("tools", kwargs)
        self.assertNotIn("tool_choice", kwargs)
        self.assertEqual(kwargs["output_config"]["format"]["type"], "json_schema")
        self.assertIs(kwargs["output_config"]["format"]["schema"], ip.RESULT_SCHEMA)
        self.assertEqual(kwargs["output_config"]["effort"], "high")

    def test_schema_follows_the_structured_output_rules(self):
        def walk(node):
            if isinstance(node, dict):
                self.assertNotIsInstance(node.get("type"), list, "nullable fields must use anyOf")
                if node.get("type") == "object":
                    self.assertIs(node.get("additionalProperties"), False)
                    self.assertEqual(sorted(node["required"]), sorted(node["properties"]))
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)
        walk(ip.RESULT_SCHEMA)
        walk(ip.RESULT_SCHEMA_LEAN)

    def test_models_that_decline_written_reasoning_get_the_lean_request(self):
        for model in LEAN_MODELS:
            with self.subTest(model=model):
                kwargs = self.request_for(model)
                schema = kwargs["output_config"]["format"]["schema"]
                system = kwargs["system"][0]
                self.assertNotIn("reasoning", schema["properties"])
                self.assertNotIn("reasoning", schema["required"])
                self.assertIn("raw_text", schema["required"])   # the transcription stays
                self.assertNotIn('2. "reasoning":', system["text"])
                self.assertNotIn("BOTH scratchpad fields", system["text"])
                for kept in ('1. "raw_text":', "Your task:", "Worked examples:"):
                    self.assertIn(kept, system["text"])
                self.assertEqual(system["cache_control"], {"type": "ephemeral"})
                self.assertIs(schema, ip.RESULT_SCHEMA_LEAN)
                self.assertEqual(system["text"], ip.SYSTEM_PROMPT_LEAN)

    def test_the_other_models_keep_the_written_reasoning(self):
        for model in FULL_MODELS:
            with self.subTest(model=model):
                kwargs = self.request_for(model)
                schema = kwargs["output_config"]["format"]["schema"]
                system = kwargs["system"][0]
                self.assertIn("reasoning", schema["properties"])
                self.assertIn("reasoning", schema["required"])
                self.assertIn('2. "reasoning":', system["text"])
                self.assertEqual(system["cache_control"], {"type": "ephemeral"})
                self.assertIs(schema, ip.RESULT_SCHEMA)
                self.assertEqual(system["text"], ip.SYSTEM_PROMPT)

    def test_the_lean_prompt_is_the_full_prompt_without_step_2(self):
        full, lean = ip.SYSTEM_PROMPT.splitlines(), ip.SYSTEM_PROMPT_LEAN.splitlines()
        start = next(i for i, line in enumerate(full) if line.startswith('2. "reasoning":'))
        end = next(i for i, line in enumerate(full) if line.startswith("Then fill the structured fields below"))
        kept = full[:start] + full[end:]
        # Every line outside step 2 survives in order; only the two lines that pointed at it changed.
        self.assertEqual(len(lean), len(kept))
        self.assertEqual([new for old, new in zip(kept, lean) if new != old], [
            "Work in this exact order, filling the scratchpad field before any structured field:",
            "Then fill the structured fields below from your transcription.",
        ])

    def test_a_model_always_gets_the_identical_system_blocks(self):
        # Prompt caching matches a byte-exact prefix, so every call must send the very same blocks.
        for model in ("claude-sonnet-5", "claude-sonnet-5-5"):
            with self.subTest(model=model):
                self.assertIs(self.request_for(model)["system"], self.request_for(model)["system"])

    def test_max_tokens_grows_for_the_top_effort_levels(self):
        for effort, expected in (("low", 16000), ("high", 16000), ("xhigh", 64000), ("max", 64000), (None, 16000)):
            _, client = self.run_image(message(answer(record())), effort=effort)
            self.assertEqual(client.calls[0]["max_tokens"], expected, effort)

    def test_effort_is_left_out_when_not_given(self):
        _, client = self.run_image(message(answer(record())), effort=None)
        self.assertNotIn("effort", client.calls[0]["output_config"])


class AnswerTests(ProcessImageCase):
    def test_full_record_is_ok(self):
        result, _ = self.run_image(message(answer(record())))
        self.assertEqual(result.status, "full_success")
        self.assertEqual(result.rows[0][:5], ["305", "Ivan", "Horvat", 1920, 1999])

    def test_refusal_is_a_billed_failure(self):
        result, _ = self.run_image(message(text="", stop_reason="refusal"))
        self.assertEqual((result.status, result.rows[0][5]), ("total_failure", "odbijeno"))
        self.assertGreater(result.cost, 0)

    def test_cut_off_answer_is_a_billed_failure(self):
        result, _ = self.run_image(message(text='{"raw_text": "IVAN', stop_reason="max_tokens"))
        self.assertEqual(result.rows[0][5], "odgovor prekinut")
        self.assertGreater(result.cost, 0)

    def test_invalid_json_is_a_billed_failure(self):
        result, _ = self.run_image(message(text="not json"))
        self.assertEqual((result.status, result.rows[0][5]), ("total_failure", "neispravan odgovor"))
        self.assertGreater(result.cost, 0)

    def test_records_of_the_wrong_shape_do_not_crash(self):
        bad = {"raw_text": "", "reasoning": "", "records": "[{}]", "error": None,
               "ambiguous_multiple_markers": False}
        result, _ = self.run_image(message(bad))
        self.assertEqual(result.rows[0][5], "neispravan odgovor")


class FileColumnTests(ProcessImageCase):
    def test_rows_end_with_the_photos_file_name(self):
        ok, _ = self.run_image(message(answer(record(), record(name="Mara"))))
        self.assertEqual([row[6] for row in ok.rows], [self.img.name, self.img.name])
        failed, _ = self.run_image(message(text="not json"))
        self.assertEqual(failed.rows[0][6], self.img.name)


class ClassificationTests(ProcessImageCase):
    def test_blank_name_counts_as_missing(self):
        result, _ = self.run_image(message(answer(record(name="  "))))
        self.assertEqual(result.status, "partial_success")
        self.assertIn("fali: ime", result.rows[0][5])

    def test_model_error_alongside_records_goes_to_review(self):
        result, _ = self.run_image(message(answer(record(), error="natpis djelomično oštećen")))
        self.assertEqual(result.status, "partial_success")
        self.assertIn("natpis djelomično oštećen", result.rows[0][5])

    def test_blank_error_without_records_is_explained(self):
        result, _ = self.run_image(message(answer(error="")))
        self.assertEqual(result.rows[0][5], "nema podataka")

    def test_implausible_years_go_to_review(self):
        for birth, death in ((1920, 20), ("19?8", 1999), (1920, 2999), (True, 1999)):
            result, _ = self.run_image(message(answer(record(birth=birth, death=death))))
            self.assertEqual(result.status, "partial_success", (birth, death))
            self.assertIn("nečitka", result.rows[0][5])

    def test_a_four_digit_string_year_is_accepted(self):
        result, _ = self.run_image(message(answer(record(birth="1920"))))
        self.assertEqual((result.status, result.rows[0][3]), ("full_success", 1920))

    def test_birth_after_death_keeps_both_years_and_is_flagged(self):
        result, _ = self.run_image(message(answer(record(birth=1987, death=1939))))
        self.assertEqual(result.status, "partial_success")
        self.assertEqual(result.rows[0][3:5], [1987, 1939])
        self.assertIn("provjeri godine", result.rows[0][5])


class NotesTests(ProcessImageCase):
    def test_system_tags_are_never_cut(self):
        rec = record(name=None, birth=None, birth_status="unreadable", death=None, death_status="unreadable")
        result = ip.process_image(FakeClient(message(answer(rec, ambiguous=True))), "claude-sonnet-5",
                                  self.img, "305", "high", ("ID iz naziva",))
        note = result.rows[0][5]
        for tag in ("fali: ime", "god. rođenja nečitka", "god. smrti nečitka",
                    "provjeri: možda više oznaka", "ID iz naziva"):
            self.assertIn(tag, note)

    def test_the_models_note_comes_last_and_is_capped(self):
        long_note = "spomenik jako oštećen, vidljiv samo donji dio ploče s natpisom " * 3
        result, _ = self.run_image(message(answer(record(birth=None, birth_status="unreadable", note=long_note))))
        note = result.rows[0][5]
        self.assertTrue(note.startswith("god. rođenja nečitka; spomenik"))
        self.assertLessEqual(len(note) - len("god. rođenja nečitka; "), 120)

    def test_failure_rows_carry_the_extra_tags(self):
        result = ip.process_image(FakeClient(message(text="x")), "claude-sonnet-5",
                                  self.img, "305", "high", ("ID iz naziva",))
        self.assertEqual(result.rows[0][5], "neispravan odgovor; ID iz naziva")


class ApiErrorTests(ProcessImageCase):
    def test_billing_error_is_fatal(self):
        body = {"type": "error", "error": {"type": "billing_error", "message": "x"}}
        result, _ = self.run_image(api_error(402, body))
        self.assertEqual(result.fatal_tag, "api-402")

    def test_a_429_without_retry_after_is_the_spend_cap(self):
        result, client = self.run_image(api_error(429))
        self.assertEqual((result.fatal_tag, len(client.calls)), ("spend-cap", 1))

    def test_a_rate_limit_with_retry_after_is_retried(self):
        result, client = self.run_image(api_error(429, headers={"retry-after": "1"}), message(answer(record())))
        self.assertEqual((result.status, len(client.calls)), ("full_success", 2))

    def test_an_overload_error_mid_stream_is_retried(self):
        body = {"type": "error", "error": {"type": "overloaded_error", "message": "x"}}
        result, client = self.run_image(MidStream(api_error(200, body)), message(answer(record())))
        self.assertEqual((result.status, len(client.calls)), ("full_success", 2))

    def test_a_connection_dropped_mid_stream_is_retried(self):
        result, _ = self.run_image(MidStream(httpx.RemoteProtocolError("peer closed")), message(answer(record())))
        self.assertEqual(result.status, "full_success")

    def test_a_bad_request_is_an_unbilled_api_failure(self):
        result, _ = self.run_image(api_error(400))
        self.assertEqual((result.api_failure, result.billed, result.fatal_tag), (True, False, None))

    def test_an_answer_is_billed(self):
        result, _ = self.run_image(message(answer(record())))
        self.assertEqual((result.api_failure, result.billed), (False, True))

    def test_an_api_failure_carries_the_extra_tags(self):
        result = ip.process_image(FakeClient(api_error(400)), "claude-sonnet-5",
                                  self.img, "305", "high", ("ID iz naziva",))
        self.assertEqual(result.rows[0][5], "greška API-ja; ID iz naziva")

    def test_exhausted_retries_are_an_api_failure_that_keeps_the_extra_tags(self):
        client = FakeClient(*[api_error(500) for _ in range(4)])
        result = ip.process_image(client, "claude-sonnet-5", self.img, "305", "high", ("ID iz naziva",))
        self.assertEqual((result.api_failure, result.billed, result.fatal_tag, len(client.calls)),
                         (True, False, None, 4))
        self.assertEqual(result.rows[0][5], "greška API-ja; ID iz naziva")
