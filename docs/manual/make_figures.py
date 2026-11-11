"""Generate the raster figures used by manual.tex.

Run from the repository root:
    .venv\\Scripts\\python.exe docs\\manual\\make_figures.py

Screenshots (fig-reader.png, fig-library.png) are produced separately by
capture_screens.py because they need a running app instance.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent / "figures"
FONT_DIR = Path("C:/Windows/Fonts")

ACCENT = (26, 86, 184)
ACCENT_LIGHT = (233, 240, 250)
CYAN = (40, 170, 220)
CYAN_LIGHT = (225, 245, 252)
AMBER = (201, 140, 30)
AMBER_LIGHT = (253, 244, 222)
INK = (23, 28, 34)
MUTED = (104, 109, 115)
LINE = (190, 198, 208)
WHITE = (255, 255, 255)


def font(size: int, bold: bool = False, mono: bool = False) -> ImageFont.FreeTypeFont:
    name = "consola.ttf" if mono else ("arialbd.ttf" if bold else "arial.ttf")
    return ImageFont.truetype(str(FONT_DIR / name), size)


def box(draw, xy, title, lines=(), fill=ACCENT_LIGHT, outline=ACCENT, title_fill=ACCENT, radius=18):
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=4)
    x0, y0, x1, _ = xy
    draw.text(((x0 + x1) / 2, y0 + 22), title, font=font(34, bold=True), fill=title_fill, anchor="ma")
    for i, line in enumerate(lines):
        mono = line.startswith("`")
        text = line.strip("`")
        draw.text(((x0 + x1) / 2, y0 + 78 + i * 40), text, font=font(26, mono=mono), fill=INK if not mono else MUTED, anchor="ma")


def arrow(draw, start, end, label="", color=ACCENT, both=False, label_offset=(0, -36)):
    draw.line([start, end], fill=color, width=5)

    def head(tip, tail):
        import math

        angle = math.atan2(tip[1] - tail[1], tip[0] - tail[0])
        size = 22
        left = (tip[0] - size * math.cos(angle - 0.45), tip[1] - size * math.sin(angle - 0.45))
        right = (tip[0] - size * math.cos(angle + 0.45), tip[1] - size * math.sin(angle + 0.45))
        draw.polygon([tip, left, right], fill=color)

    head(end, start)
    if both:
        head(start, end)
    if label:
        mid = ((start[0] + end[0]) / 2 + label_offset[0], (start[1] + end[1]) / 2 + label_offset[1])
        draw.text(mid, label, font=font(24), fill=MUTED, anchor="mm")


def architecture() -> None:
    image = Image.new("RGB", (2200, 1180), WHITE)
    draw = ImageDraw.Draw(image)
    # Browser side
    draw.rounded_rectangle((40, 40, 640, 760), radius=24, outline=LINE, width=4)
    draw.text((340, 64), "Google Chrome", font=font(30, bold=True), fill=MUTED, anchor="ma")
    box(draw, (90, 130, 590, 400), "Reader UI", ["static/index.html", "app.js / app.css", "3-pane bilingual view"])
    box(draw, (90, 470, 590, 720), "Launcher", ["Manifest V3 extension", "Alt+Shift+P"], fill=CYAN_LIGHT, outline=CYAN, title_fill=(20, 120, 170))
    # Application server
    box(draw, (820, 130, 1380, 560), "app.py", ["ThreadingHTTPServer", "`127.0.0.1:7865`", "document extraction", "job runner / library", "backup & restore"])
    box(draw, (820, 640, 1380, 900), "Native host", ["same executable", "launch / restart"], fill=CYAN_LIGHT, outline=CYAN, title_fill=(20, 120, 170))
    # Inference side
    box(draw, (1580, 130, 2150, 440), "llama-server", ["llama.cpp (CUDA / Vulkan)", "`127.0.0.1:8041`", "/v1/chat/completions"], fill=AMBER_LIGHT, outline=AMBER, title_fill=(150, 95, 10))
    box(draw, (1580, 560, 2150, 820), "PLaMo 3 Translate", ["GGUF, Q4_K_M", "models/*.gguf"], fill=AMBER_LIGHT, outline=AMBER, title_fill=(150, 95, 10))
    # Storage
    draw.ellipse((820, 960, 1380, 1020), fill=ACCENT_LIGHT, outline=ACCENT, width=4)
    draw.rectangle((822, 990, 1378, 1120), fill=ACCENT_LIGHT)
    draw.line((820, 990, 820, 1120), fill=ACCENT, width=4)
    draw.line((1380, 990, 1380, 1120), fill=ACCENT, width=4)
    draw.arc((820, 1090, 1380, 1150), 0, 180, fill=ACCENT, width=4)
    draw.text((1100, 1040), "data/  jobs · uploads · assets", font=font(30, bold=True), fill=ACCENT, anchor="mm")
    # Arrows
    arrow(draw, (590, 265), (820, 265), "HTTP / JSON", both=True)
    arrow(draw, (590, 595), (820, 770), "native messaging", color=CYAN, label_offset=(70, -20))
    arrow(draw, (1100, 640), (1100, 560), "", color=CYAN)
    arrow(draw, (1380, 285), (1580, 285), "translate", color=AMBER)
    arrow(draw, (1865, 440), (1865, 560), "", color=AMBER)
    arrow(draw, (1100, 900), (1100, 960), "", color=ACCENT)
    draw.text((1230, 930), "JSON jobs, PNG crops", font=font(24), fill=MUTED, anchor="lm")
    image.save(OUT / "fig-architecture.png", optimize=True)


def pipeline() -> None:
    steps = [
        ("PDF", "input file", ACCENT_LIGHT, ACCENT),
        ("Layout text", "pypdf visitor", ACCENT_LIGHT, ACCENT),
        ("Clean-up", "margins, captions", ACCENT_LIGHT, ACCENT),
        ("Segments", "sentence / paragraph", ACCENT_LIGHT, ACCENT),
        ("Crops", "PDFium @ 216 dpi", CYAN_LIGHT, CYAN),
        ("Translate", "PLaMo 3", AMBER_LIGHT, AMBER),
        ("Read", "UI and export", ACCENT_LIGHT, ACCENT),
    ]
    width, height = 2400, 420
    image = Image.new("RGB", (width, height), WHITE)
    draw = ImageDraw.Draw(image)
    step_w, gap = 270, 62
    x = 40
    for index, (title, subtitle, fill, outline) in enumerate(steps):
        title_color = (150, 95, 10) if outline == AMBER else (20, 120, 170) if outline == CYAN else ACCENT
        draw.rounded_rectangle((x, 110, x + step_w, 310), radius=20, fill=fill, outline=outline, width=4)
        draw.text((x + step_w / 2, 160), title, font=font(36, bold=True), fill=title_color, anchor="mm")
        draw.text((x + step_w / 2, 240), subtitle, font=font(24), fill=INK, anchor="mm")
        draw.text((x + step_w / 2, 62), f"step {index + 1}", font=font(22), fill=MUTED, anchor="mm")
        if index < len(steps) - 1:
            arrow(draw, (x + step_w + 6, 210), (x + step_w + gap - 8, 210))
        x += step_w + gap
    draw.text((width / 2, 380), "Steps 1-5 run once per upload; step 6 runs per segment with neighbouring context.", font=font(26), fill=MUTED, anchor="mm")
    image.save(OUT / "fig-pipeline.png", optimize=True)


def model_sizes() -> None:
    data = [("2B Q4_K_M", 1.81), ("8B Q4_K_M", 5.28), ("31B Q4_K_M", 19.53)]
    width, height = 1400, 900
    left, right, top, bottom = 170, 60, 70, 150
    image = Image.new("RGB", (width, height), WHITE)
    draw = ImageDraw.Draw(image)
    y_max = 22.0

    def y_of(value):
        return top + (height - top - bottom) * (1 - value / y_max)

    for tick in range(0, 23, 4):
        y = y_of(tick)
        draw.line((left, y, width - right, y), fill=(232, 236, 241), width=2)
        draw.text((left - 18, y), f"{tick}", font=font(30), fill=MUTED, anchor="rm")
    draw.line((left, top, left, height - bottom), fill=INK, width=3)
    draw.line((left, height - bottom, width - right, height - bottom), fill=INK, width=3)
    draw.text((50, (top + height - bottom) / 2), "GB", font=font(32, bold=True), fill=INK, anchor="mm")
    slot = (width - left - right) / len(data)
    for index, (label, value) in enumerate(data):
        x0 = left + slot * index + slot * 0.22
        x1 = left + slot * (index + 1) - slot * 0.22
        color = ACCENT if value < 16 else (180, 70, 60)
        draw.rectangle((x0, y_of(value), x1, height - bottom), fill=color)
        draw.text(((x0 + x1) / 2, y_of(value) - 14), f"{value:.2f}", font=font(32, bold=True), fill=INK, anchor="mb")
        draw.text(((x0 + x1) / 2, height - bottom + 24), label, font=font(32), fill=INK, anchor="ma")
    y16 = y_of(16)
    for x in range(left, width - right, 36):
        draw.line((x, y16, min(x + 20, width - right), y16), fill=AMBER, width=5)
    draw.text((left + 16, y16 - 10), "16 GB VRAM (reference GPU)", font=font(30, bold=True), fill=(150, 95, 10), anchor="lb")
    image.save(OUT / "fig-model-sizes.png", optimize=True)


def context_window() -> None:
    """Illustrate how the translation context for one sentence is assembled."""
    width, height = 1400, 760
    image = Image.new("RGB", (width, height), WHITE)
    draw = ImageDraw.Draw(image)
    draw.text((60, 50), "Paragraph P12", font=font(34, bold=True), fill=INK)
    sentences = [("S1", 0.85), ("S2", 0.6), ("S3", 0.95), ("S4", 0.7), ("S5", 0.5)]
    y = 120
    for name, fraction in sentences:
        target = name == "S3"
        fill = AMBER_LIGHT if target else ACCENT_LIGHT
        outline = AMBER if target else ACCENT
        draw.rounded_rectangle((60, y, 60 + 900 * fraction, y + 64), radius=10, fill=fill, outline=outline, width=3)
        draw.text((84, y + 32), name + ("  (being translated)" if target else ""), font=font(28, bold=target), fill=INK, anchor="lm")
        y += 92
    def bracket(y0, y1, label):
        x = 990
        draw.line((x, y0, x + 24, y0, x + 24, y1, x, y1), fill=ACCENT, width=4)
        draw.line((x + 24, (y0 + y1) / 2, x + 44, (y0 + y1) / 2), fill=ACCENT, width=4)
        draw.text((x + 56, (y0 + y1) / 2), label, font=font(28), fill=ACCENT, anchor="lm")

    bracket(120, 120 + 92 + 64, "before (last 600 chars)")
    bracket(120 + 92 * 3, 120 + 92 * 4 + 64, "after (first 600 chars)")
    draw.rounded_rectangle((60, 600, 1340, 720), radius=14, fill=CYAN_LIGHT, outline=CYAN, width=3)
    draw.text((700, 640), "context = user notes  +  before  +  after   (max 1,400 chars)", font=font(30, bold=True), fill=(20, 120, 170), anchor="mm")
    draw.text((700, 690), "sent to the model as chat_template_kwargs.context", font=font(26, mono=True), fill=MUTED, anchor="mm")
    image.save(OUT / "fig-context.png", optimize=True)


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    architecture()
    pipeline()
    model_sizes()
    context_window()
    print("figures written to", OUT)
