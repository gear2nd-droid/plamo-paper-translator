"""Capture the UI screenshots used by manual.tex.

The screenshots show this manual itself, with hand-written Japanese
translations, so they contain no third-party paper content. A throw-away
data folder is used; the user's library is never touched.

Run from the repository root (Chrome must be installed):
    .venv\\Scripts\\python.exe docs\\manual\\capture_screens.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
FIGURES = Path(__file__).resolve().parent / "figures"
CHROME = Path(os.environ.get("CHROME", r"C:\Program Files\Google\Chrome\Application\chrome.exe"))
PORT = 7871
MANUAL_JOB = "0" * 31 + "1"
DOCUMENT = "d" * 32

# (kind, heading_level, paragraph, source, translation, extra)
MANUAL_SEGMENTS = [
    ("heading", 1, 1, "1 Introduction", "1 はじめに", {}),
    ("sentence", 0, 2, "Reading papers in a second language is slow, and pasting text into an online translator breaks figures, equations and the structure of the document.",
     "第二言語で論文を読むのは時間がかかり、オンライン翻訳にテキストを貼り付けると、図や数式、文書の構造が崩れてしまいます。", {"bookmarked": True}),
    ("sentence", 0, 2, "Many papers are also confidential before publication, which rules out cloud services.",
     "また、多くの論文は公開前には機密扱いのため、クラウドサービスは使えません。", {"note": "研究室の未発表原稿はこちらで読む。"}),
    ("sentence", 0, 2, "PLaMo Paper Translator addresses both problems.",
     "PLaMo Paper Translatorは、この両方の問題を解決します。", {"clips": ["translation"]}),
    ("sentence", 0, 2, "All processing happens on the local machine, and the original structure of the paper is preserved.",
     "すべての処理はローカルのPC上で行われ、論文の元の構造も保たれます。", {}),
    ("heading", 1, 3, "2 System Overview", "2 システム概要", {}),
    ("heading", 2, 4, "2.1 Architecture", "2.1 アーキテクチャ", {}),
    ("sentence", 0, 5, "The application consists of three processes, shown in Figure 2.",
     "アプリケーションは、図2に示す3つのプロセスで構成されます。", {"clips": ["source"]}),
    ("sentence", 0, 5, "A browser displays the reader UI.",
     "ブラウザーが閲覧画面を表示します。", {}),
    ("sentence", 0, 5, "Translation itself is performed by llama-server from llama.cpp, which serves the PLaMo 3 Translate model on 127.0.0.1:8041.",
     "翻訳そのものは、llama.cppのllama-serverが担い、127.0.0.1:8041でPLaMo 3 Translateモデルを提供します。",
     {"note": "8041番ポートは外部から接続できない。", "clips": ["note"], "bookmarked": True}),
    ("figure", 0, 6, "Figure 2: Architecture. The reader UI talks to app.py over HTTP; app.py calls llama-server through its OpenAI-compatible endpoint.",
     "図2：アーキテクチャ。閲覧画面はHTTPでapp.pyと通信し、app.pyはOpenAI互換のエンドポイントを通じてllama-serverを呼び出します。",
     {"assets": [f"/api/assets/{DOCUMENT}/fig-architecture.png"]}),
    ("heading", 2, 7, "2.2 Data Storage", "2.2 データの保存", {}),
    ("sentence", 0, 8, "All user data is kept in plain files under a single folder, summarised in Table 1.",
     "ユーザーデータはすべて、表1に示す1つのフォルダー内に通常のファイルとして保存されます。", {}),
    ("heading", 2, 9, "2.3 Hardware Requirements", "2.3 ハードウェア要件", {}),
    ("sentence", 0, 10, "The reference machine has an NVIDIA RTX 2000 Ada laptop GPU with 16 GB of video memory.",
     "基準機は、16 GBのビデオメモリーを持つNVIDIA RTX 2000 Ada Laptop GPUを搭載しています。", {}),
    ("heading", 1, 11, "3 Installation", "3 インストール", {}),
    ("heading", 2, 12, "3.1 Setup Script", "3.1 セットアップスクリプト", {}),
    ("heading", 2, 13, "3.2 Chrome Launcher", "3.2 Chromeランチャー", {}),
    ("heading", 1, 14, "4 Document Pipeline", "4 文書の処理パイプライン", {}),
    ("heading", 2, 15, "4.1 Text and Layout Extraction", "4.1 テキストとレイアウトの抽出", {}),
    ("heading", 2, 16, "4.2 Segmentation", "4.2 分割", {}),
    ("heading", 2, 17, "4.3 Figures and Equations", "4.3 図と数式", {}),
    ("heading", 1, 18, "5 Translation", "5 翻訳", {}),
]

LIBRARY_EXTRA = [
    ("0" * 31 + "2", "Sample Notes on Two-Column Layouts", "completed", ["layout", "test"],
     "二段組みPDFの読み順を確認するための自作メモ。", "2026-11-09T09:30:00+00:00"),
    ("0" * 31 + "3", "Glossary Practice Document", "unread", ["glossary"],
     "", "2026-11-08T14:10:00+00:00"),
]


def segment_dict(index, kind, level, paragraph, source, translation, extra):
    return {
        "index": index, "page": 1, "source": source, "translation": translation, "error": "",
        "kind": kind, "label": "", "assets": extra.get("assets", []), "note": extra.get("note", ""),
        "bookmarked": extra.get("bookmarked", False), "clipped": bool(extra.get("clips")),
        "clips": extra.get("clips", []), "heading_level": level, "paragraph": paragraph, "sentence": 1,
        "translation_revisions": [], "translation_edited_at": "", "translation_origin": "manual",
    }


def job(job_id, title, segments, *, reading_status="unread", reading_index=0, tags=(), summary="", updated="2026-11-10T12:00:00+00:00"):
    return {
        "id": job_id, "document_id": DOCUMENT, "title": title, "status": "completed",
        "message": "翻訳が完了しました", "progress": 100, "completed": len(segments), "total": len(segments),
        "segmentation_mode": "sentence", "source_lang": "en", "target_lang": "ja", "form": "plain",
        "context": "", "glossary": [], "tags": list(tags), "paper_summary": summary, "source_url": "",
        "source_filename": f"{title}.pdf", "file_sha256": "", "reading_status": reading_status,
        "reading_index": reading_index, "last_opened_at": updated, "created_at": updated,
        "updated_at": updated, "segments": segments, "cancelled": False,
    }


def build_home(home: Path) -> None:
    jobs_dir = home / "data" / "jobs"
    assets_dir = home / "data" / "assets" / DOCUMENT
    jobs_dir.mkdir(parents=True)
    assets_dir.mkdir(parents=True)
    shutil.copy(FIGURES / "fig-architecture.png", assets_dir / "fig-architecture.png")
    segments = [segment_dict(i, *row) for i, row in enumerate(MANUAL_SEGMENTS)]
    manual = job(
        # reading_index stays 0: restoring a deeper position leaves the
        # headless screenshot blank because of the scroll restore timing.
        MANUAL_JOB, "PLaMo Paper Translator User Manual", segments, reading_status="reading", reading_index=0,
        tags=["manual", "PLaMo", "local inference"],
        summary="論文形式で書かれたPLaMo Paper Translatorの利用マニュアル。インストール、処理の流れ、翻訳、閲覧画面、ライブラリを説明する。",
    )
    (jobs_dir / f"{MANUAL_JOB}.json").write_text(json.dumps(manual, ensure_ascii=False), encoding="utf-8")
    for job_id, title, status, tags, summary, updated in LIBRARY_EXTRA:
        extra_segments = [segment_dict(0, "heading", 1, 1, "1 Introduction", "1 はじめに", {})]
        payload = job(job_id, title, extra_segments, reading_status=status, tags=tags, summary=summary, updated=updated)
        (jobs_dir / f"{job_id}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def wait_for_server() -> None:
    for _ in range(60):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/ping", timeout=1):
                return
        except OSError:
            time.sleep(0.25)
    raise RuntimeError("preview server did not start")


def screenshot(url: str, target: Path, profile: Path, size=(1440, 900)) -> None:
    subprocess.run(
        [str(CHROME), "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--user-data-dir={profile}",
         f"--window-size={size[0]},{size[1]}", "--force-device-scale-factor=2", "--virtual-time-budget=8000",
         f"--screenshot={target}", url],
        check=True, capture_output=True, timeout=120,
    )


def main() -> None:
    FIGURES.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        home = Path(directory) / "home"
        build_home(home)
        env = {**os.environ, "PLAMO_PAPER_HOME": str(home)}
        server = subprocess.Popen(
            [sys.executable, str(ROOT / "app.py"), "--port", str(PORT), "--no-browser", "--no-engine"],
            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            wait_for_server()
            reader = Path(directory) / "reader.png"
            library = Path(directory) / "library.png"
            screenshot(f"http://127.0.0.1:{PORT}/?job={MANUAL_JOB}", reader, Path(directory) / "p1")
            screenshot(f"http://127.0.0.1:{PORT}/?library=1", library, Path(directory) / "p2", size=(1100, 820))
            full = Image.open(reader).convert("RGB")
            full.save(FIGURES / "fig-reader.png", optimize=True)
            for name, box in CROPS.items():
                full.crop(box).save(FIGURES / f"{name}.png", optimize=True)
            library_image = Image.open(library).convert("RGB").crop(LIBRARY_CROP)
            mask_data_dir(library_image)
            library_image.save(FIGURES / "fig-library.png", optimize=True)
        finally:
            server.terminate()
            server.wait(timeout=10)
    print("screenshots written to", FIGURES)


def mask_data_dir(image: Image.Image) -> None:
    """Replace the temporary data path (it contains the Windows user name)."""
    from PIL import ImageDraw, ImageFont

    draw = ImageDraw.Draw(image)
    x0, y0, x1, y1 = DATA_DIR_BOX
    draw.rectangle(DATA_DIR_BOX, fill=image.getpixel((x0 - 4, y0 + 4)))
    fonts = Path("C:/Windows/Fonts")
    label = ImageFont.truetype(str(fonts / "YuGothR.ttc"), 20)
    path = ImageFont.truetype(str(fonts / "consola.ttf"), 19)
    middle = (y0 + y1) / 2
    draw.text((x0, middle), "保存先:", font=label, fill=(104, 109, 115), anchor="lm")
    draw.text((x0 + 76, middle), r"C:\PLaMoPaperTranslator\data", font=path, fill=(104, 109, 115), anchor="lm")


# Pixel boxes on the 2x screenshots, tuned for the window sizes above.
CROPS = {
    "fig-outline": (120, 264, 504, 1456),
    "fig-clips": (2112, 1268, 2840, 1770),
}
LIBRARY_CROP = (0, 0, 2200, 1640)
DATA_DIR_BOX = (92, 462, 900, 494)

if __name__ == "__main__":
    main()
