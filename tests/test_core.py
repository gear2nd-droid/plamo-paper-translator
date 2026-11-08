import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paper_core import LlamaTranslator, Segment, bilingual_html, parse_glossary, split_text


class FakeResponse:
    status = 200

    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


class CoreTests(unittest.TestCase):
    def test_split_text_respects_limit(self):
        text = "Sentence one. " * 300
        chunks = split_text(text, max_chars=180)
        self.assertGreater(len(chunks), 2)
        self.assertTrue(all(len(chunk) <= 180 for chunk in chunks))

    def test_parse_glossary(self):
        value = "foundation model = 基盤モデル\ninference => 推論\n# comment\ninvalid"
        self.assertEqual(
            parse_glossary(value),
            [
                {"source": "foundation model", "target": "基盤モデル"},
                {"source": "inference", "target": "推論"},
            ],
        )

    def test_translation_payload_uses_official_options(self):
        captured = {}

        def fake_urlopen(request, timeout=0):
            captured.update(json.loads(request.data.decode("utf-8")))
            return FakeResponse({"choices": [{"message": {"content": "基盤モデル"}}]})

        with patch("urllib.request.urlopen", fake_urlopen):
            result = LlamaTranslator().translate(
                "foundation model",
                target_lang="ja",
                source_lang="en",
                form="plain",
                context="Machine learning paper",
                glossary=[{"source": "foundation model", "target": "基盤モデル"}],
            )
        self.assertEqual(result, "基盤モデル")
        options = captured["chat_template_kwargs"]
        self.assertEqual(options["target_lang"], "ja")
        self.assertEqual(options["form"], "plain")
        self.assertEqual(options["glossary"][0]["target"], "基盤モデル")

    def test_export_escapes_source(self):
        output = bilingual_html("A&B", [Segment(0, 1, "<equation>", "式")])
        self.assertIn("A&amp;B", output)
        self.assertIn("&lt;equation&gt;", output)
        self.assertNotIn("<equation>", output)


if __name__ == "__main__":
    unittest.main()
