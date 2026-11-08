import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paper_core import (
    LlamaTranslator,
    Segment,
    _mark_caption_boundaries,
    _normalize_block,
    bilingual_html,
    neighboring_context,
    parse_glossary,
    split_document_units,
    split_text,
)


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

    def test_document_units_keep_sentences_and_structure(self):
        text = "1. Introduction\n\nJ. Smith proposed the method. It improves accuracy.\n\nE = mc^2\n\nFigure 1: Overview."
        units = split_document_units(text, include_positions=True)
        self.assertEqual(
            [kind for _, kind, _, _ in units],
            ["heading", "sentence", "sentence", "equation", "figure"],
        )
        self.assertEqual(units[1][0], "J. Smith proposed the method.")
        self.assertEqual(units[1][2:], (2, 1))
        self.assertEqual(units[2][2:], (2, 2))
        self.assertEqual(units[3][2:], (3, 1))

    def test_sentence_split_keeps_closing_quote_and_paragraph(self):
        units = split_document_units('The authors call it "robust." This is the second sentence.', include_positions=True)
        self.assertEqual([unit[0] for unit in units], ['The authors call it "robust."', "This is the second sentence."])
        self.assertEqual([unit[2:] for unit in units], [(1, 1), (1, 2)])

    def test_one_long_sentence_stays_one_display_row(self):
        sentence = "A " + "very " * 80 + "long sentence."
        units = split_document_units(sentence, max_chars=80, include_positions=True)
        self.assertEqual(len(units), 1)
        self.assertEqual(units[0][0], sentence)

    def test_neighboring_context_stays_in_same_paragraph(self):
        segments = [
            Segment(0, 1, "First.", paragraph=1, sentence=1),
            Segment(1, 1, "Second.", paragraph=1, sentence=2),
            Segment(2, 1, "Different paragraph.", paragraph=2, sentence=1),
        ]
        context = neighboring_context(segments, 1)
        self.assertIn("First.", context)
        self.assertNotIn("Different paragraph.", context)

    def test_layout_whitespace_ends_figure_caption(self):
        plain = "Figure 3: Pipeline overview. It has two stages.\nBody text begins here. It continues."
        layout = "Figure 3: Pipeline overview. It has two stages.\n\n\nBody text begins here. It continues."
        normalized = _normalize_block(_mark_caption_boundaries(plain, layout), captions_marked=True)
        units = split_document_units(normalized, include_positions=True)
        self.assertEqual([unit[0] for unit in units[:3]], ["Figure 3: Pipeline overview.", "It has two stages.", "Body text begins here."])
        self.assertEqual([unit[2] for unit in units[:3]], [1, 1, 2])

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
        output = bilingual_html("A&B", [Segment(0, 1, "<equation>", "式", note="重要", paragraph=2, sentence=3)])
        self.assertIn("A&amp;B", output)
        self.assertIn("&lt;equation&gt;", output)
        self.assertNotIn("<equation>", output)
        self.assertIn("重要", output)
        self.assertIn("TRANSLATION", output)
        self.assertIn("MEMO", output)
        self.assertIn("P2<br>S3", output)


if __name__ == "__main__":
    unittest.main()
