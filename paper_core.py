from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


LANGUAGES = {
    "auto": "自動判定",
    "en": "英語",
    "ja": "日本語",
    "zh": "中国語",
    "ko": "韓国語",
    "de": "ドイツ語",
    "fr": "フランス語",
    "es": "スペイン語",
    "it": "イタリア語",
    "pt": "ポルトガル語",
    "ru": "ロシア語",
}


@dataclass
class Segment:
    index: int
    page: int
    source: str
    translation: str = ""
    error: str = ""


def _mostly_nonlinguistic(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return True
    letters = len(re.findall(r"[A-Za-z\u3040-\u30ff\u3400-\u9fff]", compact))
    return letters / len(compact) < 0.18


def _normalize_block(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"(?<=[A-Za-z])-\n(?=[a-z])", "", text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line and not re.fullmatch(r"(?:page\s*)?\d{1,4}", line, re.I)]
    if not lines:
        return ""

    paragraphs: list[str] = []
    current: list[str] = []
    for line in lines:
        is_heading = (
            len(line) < 120
            and (
                re.match(r"^(?:abstract|introduction|background|methods?|results?|discussion|conclusion|references)\b", line, re.I)
                or re.match(r"^\d+(?:\.\d+)*\.?\s+[A-Z\u3040-\u30ff\u3400-\u9fff]", line)
            )
        )
        if is_heading and current:
            paragraphs.append(" ".join(current))
            current = []
        if is_heading:
            paragraphs.append(line)
        else:
            current.append(line)
            if re.search(r"[.!?。！？:]$", line) and len(" ".join(current)) >= 220:
                paragraphs.append(" ".join(current))
                current = []
    if current:
        paragraphs.append(" ".join(current))
    return "\n\n".join(paragraphs)


def split_text(text: str, max_chars: int = 1800) -> list[str]:
    text = text.strip()
    if not text:
        return []
    blocks = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
    chunks: list[str] = []
    current = ""

    def push(part: str) -> None:
        nonlocal current
        if not part:
            return
        if not current:
            current = part
        elif len(current) + len(part) + 2 <= max_chars:
            current += "\n\n" + part
        else:
            chunks.append(current)
            current = part

    for block in blocks:
        if len(block) <= max_chars:
            push(block)
            continue
        sentences = re.split(r"(?<=[.!?。！？])\s+", block)
        for sentence in sentences:
            sentence = sentence.strip()
            while len(sentence) > max_chars:
                cut = sentence.rfind(" ", 0, max_chars)
                if cut < max_chars // 2:
                    cut = max_chars
                push(sentence[:cut].strip())
                if current:
                    chunks.append(current)
                    current = ""
                sentence = sentence[cut:].strip()
            push(sentence)
    if current:
        chunks.append(current)
    return chunks


def extract_document(path: Path, max_chars: int = 1800) -> tuple[list[Segment], int]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError("PDF処理に必要な pypdf がありません。setup.ps1 を実行してください。") from exc
        reader = PdfReader(str(path))
        segments: list[Segment] = []
        for page_number, page in enumerate(reader.pages, start=1):
            try:
                raw = page.extract_text(extraction_mode="layout") or ""
            except TypeError:
                raw = page.extract_text() or ""
            normalized = _normalize_block(raw)
            for chunk in split_text(normalized, max_chars=max_chars):
                segments.append(Segment(index=len(segments), page=page_number, source=chunk))
        if not segments:
            raise ValueError("文字を抽出できませんでした。画像スキャンPDFはOCRしてから読み込んでください。")
        return segments, len(reader.pages)

    if suffix in {".txt", ".md"}:
        text = path.read_text(encoding="utf-8-sig")
        chunks = split_text(_normalize_block(text), max_chars=max_chars)
        if not chunks:
            raise ValueError("翻訳できる文字がありません。")
        return [Segment(index=i, page=1, source=chunk) for i, chunk in enumerate(chunks)], 1
    raise ValueError("対応形式は PDF / TXT / Markdown です。")


def parse_glossary(value: str | list[dict[str, str]]) -> list[dict[str, str]]:
    if isinstance(value, list):
        return [
            {"source": str(item.get("source", "")).strip(), "target": str(item.get("target", "")).strip()}
            for item in value
            if str(item.get("source", "")).strip() and str(item.get("target", "")).strip()
        ]
    result: list[dict[str, str]] = []
    for raw_line in value.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = re.split(r"\s*(?:=>|→|=|\t)\s*", line, maxsplit=1)
        if len(parts) == 2 and all(parts):
            result.append({"source": parts[0].strip(), "target": parts[1].strip()})
    return result


def neighboring_context(segments: list[Segment], index: int, user_context: str = "", limit: int = 1400) -> str:
    parts: list[str] = []
    if user_context.strip():
        parts.append(user_context.strip())
    if index > 0:
        parts.append("直前の原文:\n" + segments[index - 1].source[-600:])
    if index + 1 < len(segments):
        parts.append("直後の原文:\n" + segments[index + 1].source[:600])
    return "\n\n".join(parts)[:limit]


class LlamaTranslator:
    def __init__(self, base_url: str = "http://127.0.0.1:8041") -> None:
        self.base_url = base_url.rstrip("/")

    def health(self, timeout: float = 2.0) -> bool:
        try:
            with urllib.request.urlopen(self.base_url + "/health", timeout=timeout) as response:
                return response.status == 200
        except (OSError, urllib.error.URLError):
            return False

    def translate(
        self,
        text: str,
        *,
        target_lang: str,
        source_lang: str = "auto",
        form: str = "plain",
        context: str = "",
        glossary: list[dict[str, str]] | None = None,
        timeout: float = 300.0,
    ) -> str:
        if _mostly_nonlinguistic(text):
            return text
        options: dict[str, Any] = {"target_lang": target_lang}
        if source_lang and source_lang != "auto":
            options["source_lang"] = source_lang
        if target_lang == "ja" and form in {"plain", "polite"}:
            options["form"] = form
        if context:
            options["context"] = context
        if glossary:
            options["glossary"] = glossary
        max_tokens = min(2048, max(384, int(len(text) * 1.35)))
        payload = {
            "model": "plamo-3-translate",
            "messages": [{"role": "user", "content": text}],
            "temperature": 0,
            "max_tokens": max_tokens,
            "stop": ["<|plamo:tag|>", "<|plamo:bos|>"],
            "chat_template_kwargs": options,
        }
        request = urllib.request.Request(
            self.base_url + "/v1/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:800]
            raise RuntimeError(f"翻訳エンジン HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"翻訳エンジンに接続できません: {exc}") from exc
        try:
            result = data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"翻訳エンジンから予期しない応答が返りました: {data}") from exc
        return re.sub(r"(?:<\|plamo:(?:tag|bos)\|>)+$", "", result).strip()


def document_to_dict(title: str, segments: Iterable[Segment], **metadata: Any) -> dict[str, Any]:
    return {"title": title, **metadata, "segments": [asdict(segment) for segment in segments]}


def bilingual_markdown(title: str, segments: Iterable[Segment]) -> str:
    lines = [f"# {title}", "", "> PLaMo Paper Translator による対訳", ""]
    current_page = None
    for segment in segments:
        if segment.page != current_page:
            current_page = segment.page
            lines.extend([f"## Page {current_page}", ""])
        lines.extend(
            [
                "### 原文",
                "",
                segment.source,
                "",
                "### 訳文",
                "",
                segment.translation or f"⚠ {segment.error or '未翻訳'}",
                "",
            ]
        )
    return "\n".join(lines)


def bilingual_html(title: str, segments: Iterable[Segment]) -> str:
    cards = []
    for segment in segments:
        translation = segment.translation or f"⚠ {segment.error or '未翻訳'}"
        cards.append(
            '<section class="pair">'
            f'<div class="page">Page {segment.page}</div>'
            f'<article><h2>原文</h2><p>{html.escape(segment.source).replace(chr(10), "<br>")}</p></article>'
            f'<article><h2>訳文</h2><p>{html.escape(translation).replace(chr(10), "<br>")}</p></article>'
            "</section>"
        )
    safe_title = html.escape(title)
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{safe_title} — 対訳</title>
<style>
body{{font-family:"Yu Gothic UI","Noto Sans JP",sans-serif;margin:0;background:#f4f1ea;color:#20231f}}
main{{max-width:1200px;margin:auto;padding:32px}} h1{{font-size:1.7rem;margin-bottom:24px}}
.pair{{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:#d8d4c9;border:1px solid #d8d4c9;margin:0 0 18px;position:relative;break-inside:avoid}}
.pair article{{background:white;padding:22px}} .pair h2{{font-size:.75rem;letter-spacing:.12em;color:#637066;text-transform:uppercase}}
.pair p{{white-space:pre-wrap;line-height:1.8;margin:0}} .page{{position:absolute;right:10px;top:8px;font-size:.7rem;color:#788078}}
@media(max-width:760px){{.pair{{grid-template-columns:1fr}}main{{padding:16px}}}}
@media print{{body{{background:white}}main{{max-width:none;padding:0}}.pair{{margin-bottom:10px}}}}
</style></head><body><main><h1>{safe_title}</h1>{''.join(cards)}</main></body></html>"""
