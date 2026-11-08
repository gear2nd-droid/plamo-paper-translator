import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paper_core import (
    ExtractedPageImage,
    LayoutFragment,
    LlamaTranslator,
    PositionedCaption,
    Segment,
    _attach_equation_images,
    _assign_inline_images,
    _assign_page_images,
    _mark_caption_boundaries,
    _mark_equation_boundaries,
    _merge_page_continuations,
    _merge_reference_entries,
    _normalize_block,
    _positioned_equations,
    _sentence_parts,
    _strip_repeated_margin_fragments,
    bilingual_html,
    bilingual_markdown,
    document_to_dict,
    neighboring_context,
    parse_glossary,
    segments_for_mode,
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

    def test_paragraph_mode_groups_sentence_rows(self):
        segments = [
            Segment(0, 1, "First sentence.", kind="sentence", paragraph=1, sentence=1),
            Segment(1, 1, "Second sentence.", kind="sentence", paragraph=1, sentence=2),
            Segment(2, 1, "Figure 1: Sample.", kind="figure", assets=["/figure.png"], paragraph=2, sentence=1),
        ]
        grouped = segments_for_mode(segments, "paragraph")
        self.assertEqual(len(grouped), 2)
        self.assertEqual(grouped[0].source, "First sentence. Second sentence.")
        self.assertEqual(grouped[0].sentence, 0)
        self.assertEqual(grouped[1].assets, ["/figure.png"])
        self.assertEqual([segment.index for segment in grouped], [0, 1])

    def test_sentence_mode_returns_independent_copies(self):
        original = Segment(0, 1, "One.", paragraph=1, sentence=1)
        copied = segments_for_mode([original], "sentence")
        copied[0].note = "changed"
        self.assertEqual(original.note, "")

    def test_bullet_items_stay_separate(self):
        normalized = _normalize_block(
            "The method has two benefits:\n"
            "• The first benefit wraps onto\nmultiple physical lines;\n"
            "• The second benefit is concise."
        )
        units = split_document_units(normalized)
        self.assertEqual([kind for _source, kind in units], ["sentence", "list_item", "list_item"])
        self.assertEqual(units[1][0], "• The first benefit wraps onto multiple physical lines;")

    def test_page_continuation_joins_interrupted_list_item(self):
        segments = [
            Segment(0, 2, "• The pipeline is facilitated by", kind="list_item", paragraph=2, sentence=1),
            Segment(1, 3, "the robustness of modern solvers.", paragraph=3, sentence=1),
        ]
        merged = _merge_page_continuations(segments)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].source, "• The pipeline is facilitated by the robustness of modern solvers.")

    def test_references_are_grouped_as_complete_entries(self):
        segments = [
            Segment(0, 1, "REFERENCES", kind="heading", paragraph=1, sentence=1),
            Segment(1, 1, "A. Author and B. Writer.", paragraph=2, sentence=1),
            Segment(2, 1, "2024.", paragraph=3, sentence=1),
            Segment(3, 1, "A complete paper title.", paragraph=4, sentence=1),
            Segment(4, 1, "ACM Trans. Graph. 43, 4 (2024).", paragraph=5, sentence=1),
            Segment(5, 1, "C. Researcher.", paragraph=6, sentence=1),
            Segment(6, 1, "2023.", paragraph=7, sentence=1),
            Segment(7, 1, "Another title.", paragraph=8, sentence=1),
        ]
        grouped = _merge_reference_entries(segments)
        self.assertEqual([segment.kind for segment in grouped], ["heading", "citation", "citation"])
        self.assertIn("2024. A complete paper title.", grouped[1].source)

    def test_numbered_references_are_grouped_without_collapsing_section(self):
        segments = [
            Segment(0, 1, "REFERENCES", kind="heading", paragraph=1, sentence=1),
            Segment(1, 1, "[1] A. Author.", paragraph=2, sentence=1),
            Segment(2, 1, "First title.", paragraph=3, sentence=1),
            Segment(3, 1, "[2] B. Writer.", paragraph=4, sentence=1),
            Segment(4, 1, "Second title.", paragraph=5, sentence=1),
        ]
        grouped = _merge_reference_entries(segments)
        self.assertEqual([segment.kind for segment in grouped], ["heading", "citation", "citation"])
        self.assertIn("First title.", grouped[1].source)

    def test_repeated_footer_fragment_is_removed_from_end(self):
        footer = "ACM Trans. Graph., Vol. 43, No. 4, Article 85. Publication date: July 2024."
        text = "Body text mentions ACM publications.\n" + footer
        cleaned = _strip_repeated_margin_fragments(text, [(footer, "bottom")], {footer})
        self.assertEqual(cleaned.strip(), "Body text mentions ACM publications.")

    def test_academic_abbreviations_do_not_split_citation(self):
        citation = "ACM Trans. Graph., Vol. 43, No. 4, Article 85."
        self.assertEqual(_sentence_parts(citation), [citation])

    def test_figure_number_stays_with_first_caption_sentence(self):
        caption = "Fig. 12. The result of the model. A second explanation."
        self.assertEqual(
            _sentence_parts(caption),
            ["Fig. 12. The result of the model.", "A second explanation."],
        )

    def test_layout_whitespace_ends_figure_caption(self):
        plain = "Figure 3: Pipeline overview. It has two stages.\nBody text begins here. It continues."
        layout = "Figure 3: Pipeline overview. It has two stages.\n\n\nBody text begins here. It continues."
        normalized = _normalize_block(_mark_caption_boundaries(plain, layout), captions_marked=True)
        units = split_document_units(normalized, include_positions=True)
        self.assertEqual([unit[0] for unit in units[:3]], ["Figure 3: Pipeline overview.", "It has two stages.", "Body text begins here."])
        self.assertEqual([unit[2] for unit in units[:3]], [1, 1, 2])

    def test_caption_boundary_tolerates_internal_spacing_difference(self):
        plain = (
            "Fig. 2. An overview of the framework represented by a tetrahedral mesh M. "
            "The final result is shown here.\nBody text begins here."
        )
        layout_caption = (
            "Fig. 2. An overview of the framework represented by a tetrahedral meshM. "
            "The final result is shown here."
        )
        marked = _mark_caption_boundaries(plain, [layout_caption])
        self.assertEqual(marked.count("\uE100"), 1)
        normalized = _normalize_block(marked, captions_marked=True)
        units = split_document_units(normalized, include_positions=True)
        self.assertEqual(units[0][1], "figure")
        self.assertEqual(units[-1][0], "Body text begins here.")
        self.assertEqual(units[-1][1], "sentence")

    def test_images_are_assigned_to_nearest_caption_in_same_column(self):
        left = Segment(0, 1, "Fig. 1. Left figure.", kind="figure", paragraph=1, sentence=1)
        right = Segment(1, 1, "Fig. 2. Right figure.", kind="figure", paragraph=2, sentence=1)
        captions = [
            PositionedCaption(left.source, 50, 300),
            PositionedCaption(right.source, 320, 500),
        ]
        images = [
            ExtractedPageImage("/left.jpg", 50, 315, 290, 460),
            ExtractedPageImage("/right.jpg", 320, 515, 560, 690),
        ]
        unassigned = _assign_page_images([left, right], captions, images, 612)
        self.assertEqual(unassigned, [])
        self.assertEqual(left.assets, ["/left.jpg"])
        self.assertEqual(right.assets, ["/right.jpg"])

    def test_uncaptioned_inline_image_uses_nearby_figure_reference(self):
        first = Segment(0, 1, "Unrelated paragraph.", paragraph=1, sentence=1)
        target = Segment(
            1,
            1,
            "As shown in the wrapped figure on the right, support is required.",
            paragraph=2,
            sentence=1,
        )
        fragments = [
            (320, 500, "Unrelated paragraph."),
            (320, 350, "wrapped figure on the right, support"),
        ]
        image = ExtractedPageImage("/inline.jpg", 470, 300, 550, 400)
        remaining = _assign_inline_images([first, target], fragments, [image], 612)
        self.assertEqual(remaining, [])
        self.assertEqual(target.assets, ["/inline.jpg"])

    def test_numbered_display_equation_gets_stable_identity(self):
        fragments = [
            LayoutFragment(0, 80, 500, "E = mc", 10),
            LayoutFragment(1, 140, 505, "2", 7),
            LayoutFragment(2, 250, 500, " (7)", 10),
            LayoutFragment(3, 40, 450, "(7) Given a prose instruction.", 10),
        ]
        equations = _positioned_equations(fragments, 612)
        self.assertEqual([equation.number for equation in equations], [7])

        marked = _mark_equation_boundaries("E = mc 2 (7)\n(7) Given a prose instruction.", equations)
        units = split_document_units(
            _normalize_block(marked),
            include_positions=True,
            include_equation_numbers=True,
        )
        equation_unit = next(unit for unit in units if unit[4] == 7)
        self.assertEqual(equation_unit[1], "equation")
        self.assertEqual(equation_unit[0], "E = mc 2 (7)")

    def test_equation_image_only_attaches_to_matching_identity(self):
        equation = Segment(0, 1, "x = 1 (7)", kind="equation", label="equation:7")
        numbered_prose = Segment(1, 1, "(8) Given the input.", kind="list_item")
        unrelated_equation = Segment(2, 1, "y = 2 (8)", kind="equation")
        _attach_equation_images(
            [equation, numbered_prose, unrelated_equation],
            {7: "/equation-7.png", 8: "/equation-8.png"},
        )
        self.assertEqual(equation.assets, ["/equation-7.png"])
        self.assertEqual(numbered_prose.assets, [])
        self.assertEqual(unrelated_equation.assets, [])

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
        self.assertIn("Built with PLaMo", output)

    def test_equation_export_uses_image_and_collapsed_search_text(self):
        segment = Segment(
            0,
            1,
            "E = mc^2 (1)",
            "E = mc^2 (1)",
            kind="equation",
            label="equation:1",
            assets=["/equation.png"],
        )
        output = bilingual_html("Paper", [segment])
        self.assertEqual(output.count('src="/equation.png"'), 2)
        self.assertIn('class="equation-text"', output)
        self.assertIn("検索用テキスト", output)

    def test_exports_include_plamo_attribution(self):
        segments = [Segment(0, 1, "Source.", "訳文。", paragraph=1, sentence=1)]
        markdown = bilingual_markdown("Paper", segments)
        payload = document_to_dict("Paper", segments)
        self.assertIn("Built with PLaMo", markdown)
        self.assertEqual(payload["attribution"], "Built with PLaMo")
        self.assertEqual(payload["generated_by"], "PLaMo 3 Translate")
        self.assertIn("plamo-community-license", payload["model_license"])


if __name__ == "__main__":
    unittest.main()
