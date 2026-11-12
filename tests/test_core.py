import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paper_core import (
    HEADING_MARKER,
    ExtractedPageImage,
    LayoutFragment,
    LlamaTranslator,
    PositionedCaption,
    Segment,
    _attach_equation_images,
    _assign_inline_images,
    _assign_page_images,
    _extract_page_images,
    _mark_caption_boundaries,
    _mark_equation_boundaries,
    _mark_heading_boundaries,
    _merge_page_continuations,
    _merge_reference_entries,
    _normalize_block,
    _page_number_candidates_at_edges,
    _positioned_equations,
    _positioned_letter_headings,
    _apply_script_markup,
    _block_kind,
    _mark_equation_boundaries,
    _script_roles,
    _sentence_parts,
    _strip_repeated_margin_fragments,
    PositionedEquation,
    bilingual_html,
    bilingual_markdown,
    document_to_dict,
    extract_document,
    heading_level,
    neighboring_context,
    parse_glossary,
    segments_for_mode,
    split_document_units,
    split_text,
    restore_script_markup,
    strip_script_markup,
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

    def test_heading_levels_capture_section_and_subsection_structure(self):
        self.assertEqual(heading_level("1 Introduction"), 1)
        self.assertEqual(heading_level("2.3 Training Procedure"), 2)
        self.assertEqual(heading_level("2.3.1 Optimization Details"), 3)
        self.assertEqual(heading_level("1.2.1 Multi-axis 3D printing."), 3)
        self.assertEqual(heading_level("II. RELATED WORK"), 1)
        self.assertEqual(heading_level("A. Training Procedure"), 2)
        self.assertEqual(heading_level("第2節 実験方法"), 2)
        self.assertEqual(heading_level("This is an ordinary sentence."), 0)

        units = split_document_units(
            "II. RELATED WORK\n\nA. Training Procedure\n\nBody text.",
            include_positions=True,
        )
        self.assertEqual([unit[1] for unit in units], ["heading", "heading", "sentence"])

    def test_heading_segment_derives_level_for_saved_jobs(self):
        segment = Segment(0, 1, "3.2 Evaluation Protocol", kind="heading")
        self.assertEqual(segment.heading_level, 2)
        legacy_segment = Segment(1, 1, "3.2.1 Evaluation Metrics.", kind="sentence")
        self.assertEqual(legacy_segment.heading_level, 3)
        self.assertEqual(legacy_segment.kind, "heading")

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
            Segment(0, 1, "First sentence.", kind="sentence", bookmarked=True, clips=["source", "note"], paragraph=1, sentence=1),
            Segment(1, 1, "Second sentence.", kind="sentence", paragraph=1, sentence=2),
            Segment(2, 1, "Figure 1: Sample.", kind="figure", assets=["/figure.png"], paragraph=2, sentence=1),
        ]
        grouped = segments_for_mode(segments, "paragraph")
        self.assertEqual(len(grouped), 2)
        self.assertEqual(grouped[0].source, "First sentence. Second sentence.")
        self.assertEqual(grouped[0].sentence, 0)
        self.assertEqual(grouped[1].assets, ["/figure.png"])
        self.assertEqual([segment.index for segment in grouped], [0, 1])
        self.assertTrue(grouped[0].bookmarked)
        self.assertTrue(grouped[0].clipped)
        self.assertEqual(grouped[0].clips, ["source", "note"])

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

    def test_page_image_is_rendered_with_pdf_overlays_and_keeps_url(self):
        from PIL import Image

        class FakeImageFile:
            name = "/Im1.jpg"
            data = b"x" * 4096
            image = Image.new("RGB", (300, 200), "blue")

        class FakePage:
            images = [FakeImageFile()]

        class FakeBitmap:
            def to_pil(self):
                return Image.new("RGB", (640, 360), "white")

        class FakePdfiumPage:
            def __init__(self):
                self.crop = None
                self.closed = False

            def render(self, *, scale, crop):
                self.scale = scale
                self.crop = crop
                return FakeBitmap()

            def close(self):
                self.closed = True

        class FakePdfiumDocument:
            def __init__(self, rendered_page):
                self.rendered_page = rendered_page

            def __getitem__(self, index):
                self.index = index
                return self.rendered_page

        rendered_page = FakePdfiumPage()
        document = FakePdfiumDocument(rendered_page)
        with tempfile.TemporaryDirectory() as directory, patch(
            "paper_core._image_display_boxes",
            return_value={"Im1": [(50.0, 100.0, 250.0, 300.0)]},
        ):
            extracted = _extract_page_images(
                FakePage(),
                Path(directory),
                1,
                "/api/assets/document",
                pdfium_document=document,
                page_width=612.0,
                page_height=792.0,
            )
            output = Path(directory) / "page-001-figure-01.jpg"
            self.assertTrue(output.exists())
            with Image.open(output) as rendered_image:
                self.assertEqual(rendered_image.size, (640, 360))
        self.assertEqual(extracted[0].asset, "/api/assets/document/page-001-figure-01.jpg")
        self.assertEqual(document.index, 0)
        self.assertEqual(rendered_page.scale, 3)
        self.assertEqual(rendered_page.crop, (48.5, 98.5, 360.5, 490.5))
        self.assertTrue(rendered_page.closed)

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

    def test_enumerated_steps_and_equation_lines_are_not_headings(self):
        self.assertEqual(heading_level("1. create a Python virtual environment and install"), 0)
        self.assertEqual(heading_level("4. verify the GGUF header, the file size and the SHA-256"), 0)
        self.assertEqual(heading_level("72 pt/in = 3 (4)"), 0)
        self.assertEqual(heading_level("0 otherwise"), 0)
        self.assertEqual(heading_level("1. Introduction"), 1)
        self.assertEqual(heading_level("3.2 Chrome Launcher"), 2)
        self.assertEqual(split_document_units("1. create a Python virtual environment;")[0][1], "list_item")

    def test_letter_heading_needs_layout_confirmation(self):
        self.assertEqual(heading_level("A HTTP Interface"), 0)
        self.assertEqual(heading_level(HEADING_MARKER + "A HTTP Interface"), 1)
        fragments = [
            LayoutFragment(0, 54, 700, "Body text in the usual size.", 10),
            LayoutFragment(1, 54, 680, "More body text that sets the common size.", 10),
            LayoutFragment(2, 54, 650, "A HTTP Interface", 14.3),
            LayoutFragment(3, 54, 630, "A wrapped line of body text", 10),
        ]
        self.assertEqual(_positioned_letter_headings(fragments), ["A HTTP Interface"])
        marked = _mark_heading_boundaries("end of text.\nA HTTP Interface\nTable 7 lists", ["A HTTP Interface"])
        units = split_document_units(_normalize_block(marked))
        self.assertIn(("A HTTP Interface", "heading"), units)

    def test_appendix_after_references_is_not_merged_into_citations(self):
        segments = [
            Segment(0, 1, "References", kind="heading", paragraph=1, sentence=1),
            Segment(1, 1, "[1] A. Author. First title.", paragraph=2, sentence=1),
            Segment(2, 1, "[2] B. Writer. Second title.", paragraph=3, sentence=1),
            Segment(3, 2, "A HTTP Interface", kind="heading", heading_level=1, paragraph=4, sentence=1),
            Segment(4, 2, "Table 7 lists the endpoints.", paragraph=5, sentence=1),
        ]
        grouped = _merge_reference_entries(segments)
        self.assertEqual(
            [segment.kind for segment in grouped],
            ["heading", "citation", "citation", "heading", "sentence"],
        )

    def test_section_numbers_do_not_make_prose_a_table(self):
        text = "We describe installation (Section 3), the document pipeline (Section 4), translation (Section 5), the reading interface (Section 6), quality checks (Section 7) and the paper library (Section 8)."
        self.assertEqual(split_document_units(text)[0][1], "sentence")
        self.assertEqual(split_document_units("1.2 3.4 5.6 7.8 9.0 2.1")[0][1], "table")

    def test_reference_block_starting_inside_a_wrapped_url_is_prose(self):
        text = "com/docs/concepts/ native-messaging . [9] Creative Commons. Attribution 4.0 International (CC BY 4.0). [10] Creative Commons. CC0 1.0."
        units = split_document_units(text)
        self.assertTrue(any(unit[0].startswith("[9]") for unit in units))
        self.assertTrue(any(unit[0].startswith("[10]") for unit in units))

    def test_numeric_reference_entry_is_split_from_the_next_entry(self):
        text = "[1] Preferred Networks, Inc. PLaMo 3 Translate 8B (GGUF). 2026. [2] Preferred Networks, Inc. License."
        units = split_document_units(text)
        self.assertEqual([unit[1] for unit in units][:1], ["sentence"])
        self.assertTrue(any(unit[0].startswith("[2]") for unit in units))

    def test_subscript_digit_is_not_treated_as_page_number(self):
        fragments = [
            LayoutFragment(0, 300, 100, "i=", 7),
            LayoutFragment(1, 310, 70, "1", 7),
            LayoutFragment(2, 300, 60, "ρ = round(...) (11)", 10),
            LayoutFragment(3, 300, 30, "5", 8),
        ]
        candidates = [("1", "bottom"), ("5", "bottom")]
        self.assertEqual(_page_number_candidates_at_edges(candidates, fragments), [("5", "bottom")])

    def test_fraction_equation_number_shares_fragment_with_result(self):
        fragments = [
            LayoutFragment(0, 300, 640, "rendered with PDFium at a fixed scale of", 10),
            LayoutFragment(1, 402, 609.6, "σ", 10),
            LayoutFragment(2, 402, 609.6, " =", 10),
            LayoutFragment(3, 411, 609.6, " 216 dpi", 10),
            LayoutFragment(4, 422, 602.8, "72 pt/in", 10),
            LayoutFragment(5, 422, 602.8, " =", 10),
            LayoutFragment(6, 456, 609.6, " 3 (4)", 10),
            LayoutFragment(7, 316, 580, "pixels per point in the rendered image.", 10),
        ]
        equations = _positioned_equations(fragments, 612)
        self.assertEqual([equation.number for equation in equations], [4])
        marked = _mark_equation_boundaries("at\nσ = 216 dpi\n72 pt/in = 3 (4)\npixels per point.", equations)
        units = split_document_units(_normalize_block(marked), include_positions=True, include_equation_numbers=True)
        equation_unit = next(unit for unit in units if unit[4] == 4)
        self.assertEqual(equation_unit[0], "σ = 216 dpi 72 pt/in = 3 (4)")
        self.assertFalse(any(unit[1] == "heading" for unit in units))

    def test_flush_left_lead_in_word_is_left_out_of_equation(self):
        fragments = [
            LayoutFragment(0, 316, 95, "segment has index r, the progress bar", 10),
            LayoutFragment(1, 316, 83.4, "shows", 10),
            LayoutFragment(2, 423, 81.9, "(", 10),
            LayoutFragment(3, 381, 67.8, "ρ = round 100 · r", 10),
            LayoutFragment(4, 489, 67.8, " (11)", 10),
            LayoutFragment(5, 316, 50, "where the result is a percentage of the total.", 10),
        ]
        equations = _positioned_equations(fragments, 612)
        self.assertEqual(len(equations), 1)
        self.assertNotIn("shows", equations[0].text)
        self.assertGreater(equations[0].x0, 350)

    def test_lowered_and_raised_small_fragments_become_scripts(self):
        fragments = [
            LayoutFragment(0, 316, 367.4, "where", 9),
            LayoutFragment(1, 346, 367.4, " E", 9),
            LayoutFragment(2, 426, 365.7, "algo", 7),
            LayoutFragment(3, 439, 367.4, "(M) and b", 9),
            LayoutFragment(4, 470, 371.5, "⋆", 6.5),
            LayoutFragment(5, 480, 367.4, " in small caps", 9),
            LayoutFragment(6, 520, 367.4, "TEXT", 7),
        ]
        self.assertEqual(_script_roles(fragments, 612), {2: "sub", 4: "sup"})

    def test_script_parts_of_one_subscript_are_merged(self):
        fragments = [
            LayoutFragment(0, 316, 400, "For segment s", 10),
            LayoutFragment(1, 380, 398.5, "p", 7),
            LayoutFragment(2, 384, 398.5, ",", 7),
            LayoutFragment(3, 386, 398.5, "k", 7),
            LayoutFragment(4, 390, 400, " the context is", 10),
        ]
        parts = [fragment.text for fragment in fragments]
        raw = "".join(parts)
        marked = _apply_script_markup(raw, parts, list(range(len(parts))), _script_roles(fragments, 612))
        self.assertEqual(marked, "For segment s_{p,k} the context is")
        self.assertEqual(strip_script_markup(marked), raw)

    def test_script_markup_skips_fragments_missing_from_page_text(self):
        parts = ["Energy E", "(a) ", "algo", " is low"]
        raw = "Energy Ealgo is low"
        self.assertEqual(_apply_script_markup(raw, parts, [0, 1, 2, 3], {2: "sub"}), "Energy E_{algo} is low")
        self.assertEqual(_apply_script_markup("unrelated text", parts, [0, 1, 2, 3], {2: "sub"}), "unrelated text")

    def test_script_markup_does_not_change_structure_detection(self):
        self.assertEqual(heading_level("1 Introduction^{1}"), 1)
        self.assertEqual(_block_kind("where E_{algo}(M) is the printing error of M caused by the method algo."), "sentence")
        equation = PositionedEquation(1, "size(f) = b⋆, (1)", 0, 0, 1, 1)
        marked = _mark_equation_boundaries("only when\nsize( f ) = b^{⋆}, (1)\nwhere", [equation])
        self.assertIn("1:size( f ) = b^{⋆}, (1)", marked)

    def test_flattened_scripts_are_restored_in_translation(self):
        source = "where E_{algo}(M) is the error and b^{⋆} is published."
        self.assertEqual(
            restore_script_markup(source, "ここで Ealgo(M) は誤差、b⋆ は公開値。"),
            "ここで E_{algo}(M) は誤差、b^{⋆} は公開値。",
        )
        self.assertEqual(restore_script_markup(source, "E_{algo}(M) と b^{⋆}"), "E_{algo}(M) と b^{⋆}")

    def test_exports_render_scripts(self):
        segment = Segment(0, 1, "where E_{algo}(M) <is>", translation="ここで E_{algo}(M)")
        html_export = bilingual_html("T", [segment])
        self.assertIn("E<sub>algo</sub>(M) &lt;is&gt;", html_export)
        self.assertIn("E<sub>algo</sub>(M)", bilingual_markdown("T", [segment]))

    def _summary_segments(self):
        return [
            Segment(0, 1, "1 Introduction", translation="1 はじめに", kind="heading"),
            Segment(1, 1, "Figure 2: Architecture.", translation="図2：構成。", kind="figure", assets=["/api/assets/d/fig.png"]),
            Segment(2, 1, "x = 1 (4)", kind="equation", label="equation:4", assets=["/api/assets/d/eq4.png"]),
            Segment(3, 1, "Translation runs locally.", translation="翻訳はローカルで動く。"),
        ]

    def test_markdown_export_includes_portable_summary(self):
        summary = "## 結果\n図2を見る。$x_{i}$ と E_{algo}\n\n![[図2]]\n\n> 引用 [[#4]]\n\n[[表9]] ![[#4]]"
        exported = bilingual_markdown(
            "T", self._summary_segments(), summary=summary, tags=["LLM"], source_url="https://arxiv.org/abs/1", reading_status="reading",
        )
        head = exported.split("## 1 Introduction")[0]
        self.assertIn("## まとめ\n\n- タグ: LLM\n- 原文: https://arxiv.org/abs/1\n- 読書状態: 読書中", head)
        self.assertIn("### 結果", head)
        self.assertIn("$x_{i}$ と E<sub>algo</sub>", head)
        self.assertIn("![図2](/api/assets/d/fig.png)", head)
        self.assertIn("> 引用 #004", head)
        self.assertIn("表9", head)
        self.assertNotIn("[[", head)
        self.assertIn("> 翻訳はローカルで動く。（#004）", head)

    def test_markdown_export_without_summary_is_unchanged(self):
        self.assertNotIn("## まとめ", bilingual_markdown("T", self._summary_segments()))

    def test_html_export_includes_summary_renderer(self):
        static_dir = Path(__file__).resolve().parents[1] / "static"
        segments = self._summary_segments()
        exported = bilingual_html("T", segments, summary="図2と ![[式4]] </script><b>", static_dir=static_dir)
        self.assertIn('id="segment-1"', exported)
        self.assertIn('<section class="paper-summary"', exported)
        self.assertLess(exported.index("paper-summary"), exported.index('class="labels"'))
        self.assertIn("&lt;/script&gt;&lt;b&gt;", exported)
        self.assertIn('"figure:2": 1', exported)
        self.assertIn('"equation:4": 2', exported)
        self.assertIn("\\u003c/script>", exported)
        self.assertNotIn("data:font/woff2", exported)
        self.assertLess(exported.index('id="segment-3"'), exported.index("paper-summary-data"))
        with_math = bilingual_html("T", segments, summary="$x^2$", static_dir=static_dir)
        self.assertIn("data:font/woff2;base64,", with_math)
        self.assertNotIn("url(fonts/", with_math)
        self.assertNotIn("paper-summary", bilingual_html("T", segments, static_dir=static_dir))

    def test_bundled_manual_extracts_every_numbered_element(self):
        manual = Path(__file__).resolve().parents[1] / "docs" / "manual" / "manual.pdf"
        if not manual.exists():
            self.skipTest("docs/manual/manual.pdf is not present")
        with tempfile.TemporaryDirectory() as directory:
            segments, _pages = extract_document(manual, asset_dir=Path(directory), asset_url_prefix="/a")
        equations = {segment.label for segment in segments if segment.label.startswith("equation:") and segment.assets}
        self.assertEqual(equations, {f"equation:{number}" for number in range(1, 16)})
        headings = [segment.source for segment in segments if segment.kind == "heading"]
        self.assertIn("A HTTP Interface", headings)
        self.assertIn("B Troubleshooting", headings)
        self.assertFalse([heading for heading in headings if re.match(r"^\d\. ", heading)])
        self.assertFalse([heading for heading in headings if "=" in heading])
        citations = [segment.source for segment in segments if segment.kind == "citation"]
        self.assertEqual([citation.split(" ")[0] for citation in citations], [f"[{number}]" for number in range(1, 11)])
        figures = {segment.source.split(":")[0] for segment in segments if segment.kind == "figure" and segment.assets}
        self.assertEqual(figures, {f"Figure {number}" for number in range(1, 9)})
        body = " ".join(segment.source for segment in segments if segment.kind == "sentence")
        for marked in ("where b^{⋆} and h^{⋆}", "B_{p,k} = s_{p,1}", "If C_{i}⊆"):
            self.assertIn(marked, body)

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

    def test_exports_preserve_bookmarks(self):
        segment = Segment(0, 1, "1 Introduction", "1 はじめに", kind="heading", bookmarked=True, clips=["translation"])
        markdown = bilingual_markdown("Paper", [segment])
        output = bilingual_html("Paper", [segment])
        payload = document_to_dict("Paper", [segment])
        self.assertIn("★", markdown)
        self.assertIn("★ ブックマーク", output)
        self.assertTrue(payload["segments"][0]["bookmarked"])
        self.assertTrue(payload["segments"][0]["clipped"])
        self.assertEqual(payload["segments"][0]["clips"], ["translation"])


if __name__ == "__main__":
    unittest.main()
