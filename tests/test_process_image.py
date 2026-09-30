import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from extractor import image_processor as ip
from tests.helpers import FakeClient, answer, jpeg_bytes, message, record


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
    def test_uses_structured_output_not_a_forced_tool(self):
        _, client = self.run_image(message(answer(record())))
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
