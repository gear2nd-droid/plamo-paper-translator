import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

import app
import latex_ocr
from paper_core import Segment, bilingual_html, bilingual_markdown, merge_reextracted_segments


class LatexHelperTests(unittest.TestCase):
    def test_strip_equation_number(self):
        self.assertEqual(latex_ocr.strip_equation_number(r"a=b\qquad\qquad(3)"), "a=b")
        self.assertEqual(latex_ocr.strip_equation_number(r"a=b,\quad\mathrm{(12)}"), "a=b,")
        self.assertEqual(latex_ocr.strip_equation_number(r"f(x)=(a+b)"), r"f(x)=(a+b)")

    def test_repetition_is_cut_to_one_round(self):
        ids = [5, 6] + [7, 8] * 7
        self.assertTrue(latex_ocr._repeating(ids))
        self.assertEqual(latex_ocr._trim_repetition(ids), [5, 6, 7, 8])
        self.assertFalse(latex_ocr._repeating([1, 2, 3, 4, 5, 6, 7, 8]))

    def test_byte_level_alphabet_round_trip(self):
        decoder = latex_ocr._byte_decoder()
        self.assertEqual(len(decoder), 256)
        self.assertEqual(decoder["Ġ"], ord(" "))
        self.assertEqual(decoder["a"], ord("a"))

    def test_post_process_removes_spaces_latex_ignores(self):
        self.assertEqual(latex_ocr.LatexOCR.post_process(r"x _ { i } + \alpha"), r"x_{i}+\alpha")
        self.assertEqual(latex_ocr.LatexOCR.post_process(r"\mathrm { a b }"), r"\mathrm{ab}")

    def test_drop_number_cuts_a_separate_number_only(self):
        import numpy

        engine = latex_ocr.LatexOCR.__new__(latex_ocr.LatexOCR)
        engine.np = numpy
        image = Image.new("RGB", (400, 40), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((10, 10, 200, 30), fill="black")  # the formula
        draw.rectangle((360, 12, 380, 28), fill="black")  # "(1)"
        self.assertLess(engine._drop_number(image).width, 230)
        close = Image.new("RGB", (400, 40), "white")
        draw = ImageDraw.Draw(close)
        draw.rectangle((10, 10, 340, 30), fill="black")
        draw.rectangle((352, 12, 372, 28), fill="black")
        self.assertEqual(engine._drop_number(close).width, 400)
        self.assertLess(engine._drop_number(close, numbered=True).width, 360)

    def test_models_present_requires_complete_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertFalse(latex_ocr.models_present(root))
            for name in latex_ocr.MODEL_FILES:
                (root / name).write_bytes(b"x")
            self.assertFalse(latex_ocr.models_present(root))


class TexExportTests(unittest.TestCase):
    def setUp(self):
        self.segment = Segment(0, 1, "a = b (1)", "a = b (1)", kind="equation", assets=["/api/assets/x/eq.png"], tex=r"a=\frac{b}{2}", tex_origin="ocr")

    def test_markdown_export_uses_display_math(self):
        content = bilingual_markdown("Paper", [self.segment])
        self.assertIn("$$\na=\\frac{b}{2}\n$$", content)
        self.assertIn("![Figure](/api/assets/x/eq.png)", content)

    def test_html_export_shows_tex_code_and_renderer(self):
        static = Path(app.STATIC_DIR)
        content = bilingual_html("Paper", [self.segment], static_dir=static)
        self.assertIn('class="tex-code">a=\\frac{b}{2}</pre>', content)
        self.assertIn('data-tex="a=\\frac{b}{2}"', content)
        self.assertIn("katex.render", content)

    def test_html_export_without_tex_is_unchanged(self):
        plain = Segment(0, 1, "a = b (1)", "a = b (1)", kind="equation", assets=["/api/assets/x/eq.png"])
        content = bilingual_html("Paper", [plain], static_dir=Path(app.STATIC_DIR))
        self.assertNotIn("tex-code", content.split("<style>")[0] + content.split("</style>")[-1])
        self.assertNotIn("katex.render", content)

    def test_html_export_shows_figures_beside_the_translation_too(self):
        figure = Segment(0, 1, "Figure 1: A part.", "図1：部品。", kind="figure", assets=["/api/assets/x/figure.png"])
        content = bilingual_html("Paper", [figure])
        translation = content.split('<article class="translation">')[1].split("</article>")[0]
        self.assertIn('<img src="/api/assets/x/figure.png"', translation)
        self.assertIn("図1：部品。", translation)

    def test_reextraction_keeps_tex_of_unchanged_equation(self):
        fresh = Segment(0, 1, "a = b (1)", kind="equation", assets=["/api/assets/y/eq.png"])
        merged, _map, _counts = merge_reextracted_segments([self.segment], [fresh])
        self.assertEqual(merged[0].tex, r"a=\frac{b}{2}")
        self.assertEqual(merged[0].tex_origin, "ocr")


class TexApiTests(unittest.TestCase):
    def setUp(self):
        self.job_id = "b" * 32
        self.job = {
            "id": self.job_id,
            "status": "completed",
            "segments": [
                Segment(0, 1, "Text", "訳"),
                Segment(1, 1, "a = b (1)", "a = b (1)", kind="equation", assets=["/api/assets/x/eq.png"]),
            ],
        }

    def tearDown(self):
        app.jobs.pop(self.job_id, None)
        app.latex_pending.discard(self.job_id)

    def test_manual_tex_is_saved_and_validated(self):
        segment = app._set_segment_tex(self.job, 1, "  x^2  ")
        self.assertEqual((segment.tex, segment.tex_origin), ("x^2", "manual"))
        with self.assertRaises(ValueError):
            app._set_segment_tex(self.job, 0, "x")
        with self.assertRaises(IndexError):
            app._set_segment_tex(self.job, 5, "x")
        with self.assertRaises(ValueError):
            app._set_segment_tex(self.job, 1, 3)

    def test_no_queue_without_models(self):
        app.jobs[self.job_id] = self.job
        with tempfile.TemporaryDirectory() as directory, patch.object(app, "LATEX_MODEL_DIR", Path(directory)):
            self.assertFalse(app._queue_latex(self.job_id))
            result = app._job_tex(self.job_id, self.job)
        self.assertEqual(result["items"], {})
        self.assertFalse(result["available"])

    def test_recognition_fills_rows_and_saves(self):
        app.jobs[self.job_id] = self.job

        class FakeEngine:
            def __call__(self, image, numbered=False):
                return r"a=b\qquad(1)" if numbered else "wrong"

        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "eq.png"
            Image.new("RGB", (40, 20), "white").save(image)
            with (
                patch.object(app, "JOB_DIR", Path(directory)),
                patch.object(app.latex_ocr, "engine", return_value=FakeEngine()),
                patch.object(app, "_asset_path", return_value=image),
            ):
                app._recognize_job_equations(self.job_id)
                saved = json.loads((Path(directory) / f"{self.job_id}.json").read_text(encoding="utf-8"))
        self.assertEqual(self.job["segments"][1].tex, "a=b")
        self.assertEqual(saved["segments"][1]["tex_origin"], "ocr")
        self.assertEqual(self.job["segments"][0].tex, "")


if __name__ == "__main__":
    unittest.main()
