from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable


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

CAPTION_MARKER = "\uE100"
BODY_MARKER = "\uE101"
PLAMO_LICENSE_URL = "https://plamo.preferredai.jp/info/plamo-community-license-ja"
PLAMO_ATTRIBUTION = "Built with PLaMo"

LIST_PATTERN = re.compile(r"^(?:[•●◦▪‣*-]\s+|\((?:[1-9]|1[01])\)\s+|(?:[1-9]|1[01])[.)]\s+)")

POSITIONED_CAPTION_PATTERN = re.compile(r"^(?:figure|fig\.?|table)\s*\d+\s*[.:]", re.I)


@dataclass
class Segment:
    index: int
    page: int
    source: str
    translation: str = ""
    error: str = ""
    kind: str = "sentence"
    label: str = ""
    assets: list[str] = field(default_factory=list)
    note: str = ""
    paragraph: int = 0
    sentence: int = 0


@dataclass(frozen=True)
class PositionedCaption:
    text: str
    x: float
    y: float


@dataclass(frozen=True)
class ExtractedPageImage:
    asset: str
    x0: float | None = None
    y0: float | None = None
    x1: float | None = None
    y1: float | None = None


def _mostly_nonlinguistic(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return True
    letters = len(re.findall(r"[A-Za-z\u3040-\u30ff\u3400-\u9fff]", compact))
    return letters / len(compact) < 0.18


def _normalize_block(text: str, *, captions_marked: bool = False) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"(?<=[A-Za-z])-\n(?=[a-z])", "", text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    if not any(lines):
        return ""

    paragraphs: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            paragraphs.append(" ".join(current))
            current.clear()

    for line in lines:
        if not line:
            flush()
            continue
        if re.fullmatch(r"(?:page\s*)?\d{1,4}", line, re.I):
            continue
        verified_caption = line.startswith(CAPTION_MARKER)
        if verified_caption:
            line = CAPTION_MARKER + line.removeprefix(CAPTION_MARKER).lstrip()
        elif captions_marked and re.match(r"^(?:figure|fig\.|table)\s*\d+[.:]", line, re.I):
            # A body sentence can legitimately begin with "Figure 8.". Only
            # position-verified captions receive figure/table treatment in PDF.
            line = BODY_MARKER + line
        special_kind = _block_kind(line)
        if special_kind == "list_item":
            flush()
            current.append(line)
            if re.search(r"[.!?。！？;；]$", line):
                flush()
            continue
        if current and _block_kind(current[0]) == "list_item":
            if special_kind not in {"heading", "figure", "table", "equation"}:
                current.append(line)
                if re.search(r"[.!?。！？;；]$", line):
                    flush()
                continue
            flush()
        is_caption = verified_caption or (
            not captions_marked and bool(re.match(r"^(?:figure|fig\.|table)\s*\d+[.:]", line, re.I))
        )
        if is_caption:
            flush()
            current.append(line)
            continue
        if (
            current
            and re.match(
                r"^(?:figure|fig\.|table)\s*\d+[.:]",
                current[0].removeprefix(CAPTION_MARKER),
                re.I,
            )
            and special_kind not in {"heading", "equation", "table"}
        ):
            # PDF captions are commonly wrapped across physical lines. Keep the
            # whole caption as one paragraph; sentence splitting happens later.
            current.append(line)
            continue
        if special_kind in {"equation", "table"}:
            flush()
            paragraphs.append(line)
            continue
        is_heading = (
            len(line) < 120
            and (
                re.match(r"^(?:abstract|introduction|background|methods?|results?|discussion|conclusion|references)\b", line, re.I)
                or re.match(r"^\d+(?:\.\d+)*\.?\s+[A-Z\u3040-\u30ff\u3400-\u9fff]", line)
            )
        )
        if is_heading:
            flush()
        if is_heading:
            paragraphs.append(line)
        else:
            current.append(line)
            if re.search(r"[.!?。！？:]$", line) and len(" ".join(current)) >= 220:
                flush()
    flush()
    return "\n\n".join(paragraphs)


def _positioned_captions(
    fragments: list[tuple[float, float, str]],
    page_midpoint: float,
) -> list[PositionedCaption]:
    captions: list[PositionedCaption] = []
    for start, (start_x, start_y, start_text) in enumerate(fragments):
        if not POSITIONED_CAPTION_PATTERN.match(start_text.strip()):
            continue
        left_column = start_x < page_midpoint
        previous_y = start_y
        pieces = [start_text]
        for x, y, value in fragments[start + 1 :]:
            if (x < page_midpoint) != left_column:
                break
            if y > previous_y + 3 or previous_y - y > 18:
                break
            if POSITIONED_CAPTION_PATTERN.match(value.strip()):
                break
            pieces.append(value)
            if previous_y - y > 2:
                previous_y = y
        caption = re.sub(r"\s+", " ", "".join(pieces)).strip()
        if caption:
            captions.append(PositionedCaption(caption, start_x, start_y))
    return captions


def _positioned_caption_blocks(
    fragments: list[tuple[float, float, str]],
    page_midpoint: float,
) -> list[str]:
    return [caption.text for caption in _positioned_captions(fragments, page_midpoint)]


def _mark_caption_boundaries(text: str, caption_source: str | list[str]) -> str:
    """Use layout whitespace to restore caption boundaries in plain extraction."""
    caption_blocks = caption_source if isinstance(caption_source, list) else []
    if isinstance(caption_source, str):
        for raw_block in re.split(r"\n\s*\n+", caption_source):
            lines = [re.sub(r"[ \t]+", " ", line).strip() for line in raw_block.splitlines() if line.strip()]
            if lines and re.match(r"^(?:figure|fig\.|table)\s*\d+[.:]", lines[0], re.I):
                caption_blocks.append(" ".join(lines))
    for caption in caption_blocks:
        tokens = caption.split()
        if not tokens:
            continue
        pattern = r"\s+".join(re.escape(token) for token in tokens)
        text, replacements = re.subn(
            pattern,
            lambda match: f"\n\n{CAPTION_MARKER}{match.group(0)}\n\n",
            text,
            count=1,
        )
        if replacements:
            continue
        # Text extraction can insert or remove spaces around equations and
        # inline symbols. Match stable anchors at both ends so the complete
        # layout-verified caption still becomes its own paragraph.
        for anchor_size in (8, 6, 4):
            if len(tokens) < anchor_size * 2:
                continue
            start_pattern = r"\s*".join(re.escape(token) for token in tokens[:anchor_size])
            end_pattern = r"\s*".join(re.escape(token) for token in tokens[-anchor_size:])
            fuzzy_pattern = rf"{start_pattern}[\s\S]{{0,5000}}?{end_pattern}"
            text, replacements = re.subn(
                fuzzy_pattern,
                lambda match: f"\n\n{CAPTION_MARKER}{match.group(0)}\n\n",
                text,
                count=1,
            )
            if replacements:
                break
    return text


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


def _block_kind(text: str) -> str:
    compact = text.strip()
    if compact.startswith(BODY_MARKER):
        return "sentence"
    compact = compact.removeprefix(CAPTION_MARKER)
    if re.match(
        r"^(?:abstract|introduction|background|related work|methods?|materials?|results?|discussion|conclusions?|references|acknowledg(?:e)?ments?)\s*$",
        compact,
        re.I,
    ) or re.match(r"^\d+(?:\.\d+)*\.?\s+[A-Z\u3040-\u30ff\u3400-\u9fff][^.!?]{0,100}$", compact):
        return "heading"
    if LIST_PATTERN.match(compact):
        return "list_item"
    if re.match(r"^(?:figure|fig\.)\s*\d+[.:]", compact, re.I):
        return "figure"
    if re.match(r"^table\s*\d+[.:]", compact, re.I):
        return "table"
    math_symbols = len(re.findall(r"[=∑∫√±≤≥≈≠∞∂∇α-ωΑ-Ω^_{}|]", compact))
    letters = len(re.findall(r"[A-Za-z\u3040-\u30ff\u3400-\u9fff]", compact))
    if math_symbols >= 2 and (letters < 30 or math_symbols / max(len(compact), 1) > 0.08):
        return "equation"
    numeric_tokens = len(re.findall(r"(?<!\w)[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?!\w)", compact))
    if numeric_tokens >= 5 and numeric_tokens >= max(3, len(compact.split()) // 4):
        return "table"
    return "sentence"


def _sentence_parts(text: str) -> list[str]:
    marker = "\uE000"
    boundary = "\uE001"
    protected = re.sub(r"(?<=\d)\.(?=\d)", marker, text)
    protected = re.sub(
        r"^((?:fig(?:ure)?\.?|table)\s+\d+)\.(?=\s)",
        lambda match: match.group(1) + marker,
        protected,
        flags=re.I,
    )
    protected = re.sub(r"\b([A-Z])\.(?=\s+[A-Z])", lambda match: match.group(1) + marker, protected)
    protected = re.sub(
        r"\b(?:e\.g|i\.e|et\s+al|Fig|Figs|Eq|Eqs|Sec|Secs|Dr|Prof|No|Nos|Vol|Trans|Proc|Conf|Comput|Graph|Int|pp|eds?)\s*\.",
        lambda match: match.group(0).replace(".", marker),
        protected,
        flags=re.I,
    )
    protected = re.sub(
        r"([.!?。！？][\"'”’\)\]]*)\s+(?=[\"'“‘(\[]?[A-Z0-9\u3040-\u30ff\u3400-\u9fff])",
        rf"\1{boundary}",
        protected,
    )
    parts = protected.split(boundary)
    return [part.replace(marker, ".").strip() for part in parts if part.strip()]


def split_document_units(
    text: str,
    max_chars: int = 1000,
    *,
    include_positions: bool = False,
) -> list[tuple[Any, ...]]:
    units: list[tuple[Any, ...]] = []
    paragraph = 0
    for block in (part.strip() for part in re.split(r"\n\s*\n", text)):
        if not block:
            continue
        paragraph += 1
        kind = _block_kind(block)
        force_sentence = block.startswith(BODY_MARKER)
        clean_block = block.replace(CAPTION_MARKER, "").replace(BODY_MARKER, "")
        split_as_sentences = kind in {"sentence", "figure"} or (
            kind == "table" and re.match(r"^Table\s*\d+", clean_block, re.I)
        )
        parts = _sentence_parts(clean_block) if split_as_sentences else [clean_block]
        sentence = 0
        for part in parts:
            sentence += 1
            # A display row is a linguistic sentence. Do not split a long
            # sentence just to satisfy a transport-size preference.
            chunks = [part] if split_as_sentences else split_text(part, max_chars=max_chars)
            for chunk_number, chunk in enumerate(chunks, start=1):
                resolved_kind = "sentence" if force_sentence else (kind if kind != "sentence" else _block_kind(chunk))
                position = sentence if len(chunks) == 1 else sentence * 100 + chunk_number
                units.append((chunk, resolved_kind, paragraph, position) if include_positions else (chunk, resolved_kind))
    return units


def segments_for_mode(segments: Iterable[Segment], mode: str) -> list[Segment]:
    """Return independent sentence rows or consecutive paragraph rows."""
    source_segments = list(segments)
    if mode == "sentence":
        return [Segment(**segment.__dict__) for segment in source_segments]
    if mode != "paragraph":
        raise ValueError("分割モードが不正です。")

    grouped: list[list[Segment]] = []
    for segment in source_segments:
        if grouped and segment.paragraph and grouped[-1][0].paragraph == segment.paragraph:
            grouped[-1].append(segment)
        else:
            grouped.append([segment])

    result: list[Segment] = []
    for members in grouped:
        first = members[0]
        kinds = {member.kind for member in members}
        result.append(
            Segment(
                index=len(result),
                page=first.page,
                source=" ".join(member.source.strip() for member in members if member.source.strip()),
                translation=" ".join(member.translation.strip() for member in members if member.translation.strip()),
                error="; ".join(member.error for member in members if member.error),
                kind=first.kind if len(kinds) == 1 else "sentence",
                label=next((member.label for member in members if member.label), ""),
                assets=[asset for member in members for asset in member.assets],
                note="\n".join(member.note for member in members if member.note),
                paragraph=first.paragraph,
                sentence=0,
            )
        )
    return result


def _image_display_boxes(page: Any) -> dict[str, list[tuple[float, float, float, float]]]:
    boxes: dict[str, list[tuple[float, float, float, float]]] = {}

    def collect_image(operation: bytes, operands: list[Any], cm: list[float], _tm: list[float]) -> None:
        if operation != b"Do" or not operands:
            return
        name = str(operands[0]).lstrip("/")
        if len(cm) < 6:
            return
        a, b, c, d, e, f = (float(value) for value in cm[:6])
        corners = ((e, f), (a + e, b + f), (c + e, d + f), (a + c + e, b + d + f))
        xs = [point[0] for point in corners]
        ys = [point[1] for point in corners]
        boxes.setdefault(name, []).append((min(xs), min(ys), max(xs), max(ys)))

    try:
        page.extract_text(visitor_operand_before=collect_image)
    except (TypeError, ValueError):
        return {}
    return boxes


def _extract_page_images(
    page: Any,
    asset_dir: Path | None,
    page_number: int,
    url_prefix: str,
) -> list[ExtractedPageImage]:
    if asset_dir is None:
        return []
    assets: list[ExtractedPageImage] = []
    try:
        images = page.images
        display_boxes = _image_display_boxes(page)
        asset_dir.mkdir(parents=True, exist_ok=True)
        for image_number, image_file in enumerate(images, start=1):
            image = image_file.image
            width, height = image.size
            if width < 120 or height < 90 or width * height < 60_000:
                continue
            data = image_file.data
            if len(data) < 4_096:
                continue
            suffix = Path(image_file.name).suffix.lower()
            if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}:
                suffix = ".png"
                from io import BytesIO

                buffer = BytesIO()
                image.save(buffer, format="PNG")
                data = buffer.getvalue()
            filename = f"page-{page_number:03d}-figure-{image_number:02d}{suffix}"
            (asset_dir / filename).write_bytes(data)
            resource_name = Path(image_file.name).stem.lstrip("/")
            box = next(iter(display_boxes.get(resource_name, [])), None)
            asset = f"{url_prefix.rstrip('/')}/{filename}"
            assets.append(ExtractedPageImage(asset, *(box or (None, None, None, None))))
    except (ImportError, OSError, ValueError):
        return []
    return assets


def _caption_identity(text: str) -> tuple[str, int] | None:
    match = re.match(r"^(figure|fig\.?)\s*(\d+)\s*[.:]", text.strip(), re.I)
    if match:
        return "figure", int(match.group(2))
    match = re.match(r"^table\s*(\d+)\s*[.:]", text.strip(), re.I)
    if match:
        return "table", int(match.group(1))
    return None


def _assign_page_images(
    page_segments: list[Segment],
    captions: list[PositionedCaption],
    images: list[ExtractedPageImage],
    page_width: float,
) -> list[ExtractedPageImage]:
    leaders: dict[tuple[str, int], Segment] = {}
    for segment in page_segments:
        identity = _caption_identity(segment.source)
        if identity and identity not in leaders:
            leaders[identity] = segment

    figure_captions = [
        caption for caption in captions if (_caption_identity(caption.text) or (None, 0))[0] == "figure"
    ]
    unassigned: list[ExtractedPageImage] = []
    for image in images:
        candidates = figure_captions
        if image.x0 is not None and image.x1 is not None and image.y0 is not None:
            image_width = image.x1 - image.x0
            if image_width < page_width * 0.65:
                image_is_left = (image.x0 + image.x1) / 2 < page_width / 2
                same_column = [caption for caption in candidates if (caption.x < page_width / 2) == image_is_left]
                if same_column:
                    candidates = same_column
            captions_below = [caption for caption in candidates if caption.y <= image.y0 + 8]
            if captions_below:
                candidates = captions_below
            if candidates:
                caption = min(candidates, key=lambda item: abs(image.y0 - item.y))
            else:
                caption = None
        else:
            caption = next(
                (item for item in candidates if (identity := _caption_identity(item.text)) in leaders and not leaders[identity].assets),
                None,
            )

        identity = _caption_identity(caption.text) if caption else None
        leader = leaders.get(identity) if identity else None
        if leader:
            leader.assets.append(image.asset)
        else:
            unassigned.append(image)
    return unassigned


def _word_tokens(value: str) -> set[str]:
    return {word.lower() for word in re.findall(r"[A-Za-z]{3,}", value)}


def _assign_inline_images(
    page_segments: list[Segment],
    fragments: list[tuple[float, float, str]],
    images: list[ExtractedPageImage],
    page_width: float,
) -> list[str]:
    remaining: list[str] = []
    for image in images:
        if image.x0 is None or image.x1 is None or image.y0 is None or image.y1 is None:
            remaining.append(image.asset)
            continue
        image_is_left = (image.x0 + image.x1) / 2 < page_width / 2
        nearby = [
            (x, y, value)
            for x, y, value in fragments
            if value.strip() and (x < page_width / 2) == image_is_left
        ]
        if not nearby:
            remaining.append(image.asset)
            continue

        def fragment_rank(fragment: tuple[float, float, str]) -> tuple[int, float, float]:
            _x, y, value = fragment
            if image.y0 <= y <= image.y1:
                vertical_gap = 0.0
            else:
                vertical_gap = min(abs(y - image.y0), abs(y - image.y1))
            mentions_nearby_figure = vertical_gap <= 100 and bool(
                re.search(r"\b(?:fig(?:ure)?|illustration|image|shown|above|right|left)\b", value, re.I)
            )
            return (0 if mentions_nearby_figure else 1, vertical_gap, abs(y - (image.y0 + image.y1) / 2))

        anchor_text = min(nearby, key=fragment_rank)[2]
        anchor_words = _word_tokens(anchor_text)
        if not anchor_words:
            remaining.append(image.asset)
            continue

        def segment_score(segment: Segment) -> tuple[float, int]:
            source_words = _word_tokens(segment.source)
            overlap = len(anchor_words & source_words) / len(anchor_words)
            return overlap, len(anchor_words & source_words)

        leader = max(page_segments, key=segment_score, default=None)
        if leader is None or segment_score(leader)[0] < 0.6:
            remaining.append(image.asset)
            continue
        leader.assets.append(image.asset)
    return remaining


def _margin_key(value: str) -> str:
    compact = re.sub(r"\s+", " ", value).strip()
    if re.fullmatch(r"(?:\d+:)?\d+", compact):
        return "__page_number__"
    return compact


def _remove_fragment_once(text: str, value: str, *, from_end: bool) -> str:
    tokens = value.split()
    if not tokens:
        return text
    pattern = r"\s+".join(re.escape(token) for token in tokens)
    matches = list(re.finditer(pattern, text))
    if not matches:
        return text
    match = matches[-1] if from_end else matches[0]
    return text[: match.start()] + "\n" + text[match.end() :]


def _strip_repeated_margin_fragments(
    text: str,
    candidates: list[tuple[str, str]],
    repeated_keys: set[str],
) -> str:
    for value, zone in candidates:
        if _margin_key(value) in repeated_keys:
            text = _remove_fragment_once(text, value, from_end=zone == "bottom")
    return text


def _merge_page_continuations(segments: list[Segment]) -> list[Segment]:
    result = list(segments)
    index = 0
    while index + 1 < len(result):
        current = result[index]
        following = result[index + 1]
        crosses_page = current.page != following.page
        can_join = (
            crosses_page
            and current.kind in {"sentence", "list_item", "figure", "citation"}
            and following.kind in {"sentence", "list_item"}
            and current.label != "asset"
            and following.label != "asset"
            and not re.search(r"[.!?。！？;；:]\s*[\"'”’\)\]]*$", current.source)
        )
        if not can_join:
            index += 1
            continue
        if current.source.endswith("-") and re.match(r"^[a-z]", following.source):
            current.source = current.source[:-1] + following.source
        else:
            current.source = current.source.rstrip() + " " + following.source.lstrip()
        current.assets.extend(following.assets)
        del result[index + 1]
    return result


def _merge_interrupted_sentences(segments: list[Segment]) -> list[Segment]:
    result = list(segments)
    index = 0
    while index < len(result):
        current = result[index]
        if current.kind not in {"sentence", "list_item"} or re.search(
            r"[.!?。！？;；:]\s*[\"'”’\)\]]*$", current.source
        ):
            index += 1
            continue
        following_index = index + 1
        while following_index < len(result) and result[following_index].kind in {"figure", "table"}:
            following_index += 1
        if following_index == index + 1 or following_index >= len(result):
            index += 1
            continue
        following = result[following_index]
        if following.page != current.page or following.kind not in {"sentence", "list_item"}:
            index += 1
            continue
        if current.kind != "list_item" and not re.match(r"^[a-z]", following.source):
            index += 1
            continue
        current.source = re.sub(r"\(see(?:\s+\([a-z]\)){2,}\s*$", "(see", current.source, flags=re.I)
        current.source = current.source.rstrip() + " " + following.source.lstrip()
        current.assets.extend(following.assets)
        del result[following_index]
        index += 1
    return result


def _merge_reference_entries(segments: list[Segment]) -> list[Segment]:
    reference_index = next(
        (
            index
            for index, segment in enumerate(segments)
            if segment.kind == "heading" and segment.source.strip().lower() in {"reference", "references"}
        ),
        None,
    )
    if reference_index is None:
        return segments

    result = list(segments[: reference_index + 1])
    tail = segments[reference_index + 1 :]
    current: list[Segment] = []
    year_pattern = re.compile(r"^(?:18|19|20)\d{2}[a-z]?\.$")
    numbered_pattern = re.compile(r"^(?:\[\d+\]|\d+[.)])\s+")
    year_count = sum(bool(year_pattern.fullmatch(segment.source.strip())) for segment in tail)
    numbered_count = sum(bool(numbered_pattern.match(segment.source.strip())) for segment in tail)
    if year_count < 2 and numbered_count < 2:
        return segments

    def flush() -> None:
        if not current:
            return
        first = current[0]
        result.append(
            Segment(
                index=0,
                page=first.page,
                source=" ".join(segment.source.strip() for segment in current if segment.source.strip()),
                kind="citation",
                assets=[asset for segment in current for asset in segment.assets],
                paragraph=first.paragraph,
                sentence=1,
            )
        )
        current.clear()

    for index, segment in enumerate(tail):
        next_is_year = index + 1 < len(tail) and bool(year_pattern.fullmatch(tail[index + 1].source.strip()))
        if segment.label == "asset":
            flush()
            result.append(segment)
        elif (year_count >= 2 and next_is_year and current) or (
            year_count < 2 and numbered_pattern.match(segment.source.strip()) and current
        ):
            flush()
            current.append(segment)
        else:
            current.append(segment)
    flush()
    return result


def _reindex_segments(segments: list[Segment]) -> list[Segment]:
    paragraph_number = 0
    sentence_number = 0
    previous_paragraph: int | None = None
    for index, segment in enumerate(segments):
        if segment.paragraph != previous_paragraph:
            paragraph_number += 1
            sentence_number = 0
            previous_paragraph = segment.paragraph
        sentence_number += 1
        segment.index = index
        segment.paragraph = paragraph_number
        segment.sentence = sentence_number
    return segments


def extract_document(
    path: Path,
    max_chars: int = 1000,
    *,
    asset_dir: Path | None = None,
    asset_url_prefix: str = "",
) -> tuple[list[Segment], int]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError("PDF処理に必要な pypdf がありません。setup.ps1 を実行してください。") from exc
        reader = PdfReader(str(path))
        page_records: list[tuple[Any, str, list[tuple[float, float, str]], list[tuple[str, str]]]] = []
        margin_counts: Counter[str] = Counter()
        for page in reader.pages:
            fragments: list[tuple[float, float, str]] = []
            margin_candidates: list[tuple[str, str]] = []
            page_height = float(page.mediabox.height)
            try:

                def collect_fragment(value: str, _cm: Any, tm: list[float], _font: Any, size: float) -> None:
                    if not value.strip():
                        return
                    x, y = float(tm[4]), float(tm[5])
                    fragments.append((x, y, value))
                    if float(size) <= 8.5 and 10 < y < 85:
                        margin_candidates.append((value, "bottom"))
                    elif float(size) <= 8.5 and page_height - 65 < y <= page_height:
                        margin_candidates.append((value, "top"))

                raw = page.extract_text(visitor_text=collect_fragment) or ""
            except TypeError:
                fragments = []
                margin_candidates = []
                raw = page.extract_text() or ""
            margin_counts.update({_margin_key(value) for value, _zone in margin_candidates if value.strip()})
            page_records.append((page, raw, fragments, margin_candidates))

        repeated_margin_keys = {key for key, count in margin_counts.items() if count >= 2}
        repeated_margin_keys.add("__page_number__")
        segments: list[Segment] = []
        paragraph_offset = 0
        for page_number, (page, raw, fragments, margin_candidates) in enumerate(page_records, start=1):
            raw = _strip_repeated_margin_fragments(raw, margin_candidates, repeated_margin_keys)
            positioned_captions: list[PositionedCaption] = []
            if fragments:
                positioned_captions = _positioned_captions(fragments, float(page.mediabox.width) / 2)
                raw = _mark_caption_boundaries(raw, [caption.text for caption in positioned_captions])
            normalized = _normalize_block(raw, captions_marked=bool(fragments))
            page_start = len(segments)
            units = split_document_units(normalized, max_chars=max_chars, include_positions=True)
            for chunk, kind, paragraph, sentence in units:
                segments.append(
                    Segment(
                        index=len(segments),
                        page=page_number,
                        source=chunk,
                        kind=kind,
                        paragraph=paragraph_offset + paragraph,
                        sentence=sentence,
                    )
                )
            paragraph_offset += max((unit[2] for unit in units), default=0)
            images = _extract_page_images(page, asset_dir, page_number, asset_url_prefix)
            if images:
                unassigned_images = _assign_page_images(
                    segments[page_start:],
                    positioned_captions,
                    images,
                    float(page.mediabox.width),
                )
                unassigned_assets = _assign_inline_images(
                    segments[page_start:],
                    fragments,
                    unassigned_images,
                    float(page.mediabox.width),
                )
                if unassigned_assets:
                    segments.append(
                        Segment(
                            index=len(segments),
                            page=page_number,
                            source=f"Figures extracted from source page {page_number}",
                            translation=f"原文ページ {page_number} から抽出した図",
                            kind="figure",
                            label="asset",
                            assets=unassigned_assets,
                            paragraph=paragraph_offset + 1,
                            sentence=1,
                        )
                    )
                    paragraph_offset += 1
        if not segments:
            raise ValueError("文字を抽出できませんでした。画像スキャンPDFはOCRしてから読み込んでください。")
        segments = _merge_page_continuations(segments)
        segments = _merge_interrupted_sentences(segments)
        segments = _merge_reference_entries(segments)
        return _reindex_segments(segments), len(reader.pages)

    if suffix in {".txt", ".md"}:
        text = path.read_text(encoding="utf-8-sig")
        units = split_document_units(_normalize_block(text), max_chars=max_chars, include_positions=True)
        if not units:
            raise ValueError("翻訳できる文字がありません。")
        return [
            Segment(index=i, page=1, source=chunk, kind=kind, paragraph=paragraph, sentence=sentence)
            for i, (chunk, kind, paragraph, sentence) in enumerate(units)
        ], 1
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
    paragraph = segments[index].paragraph
    if paragraph:
        before = [segment.source for segment in segments[:index] if segment.paragraph == paragraph]
        after = [segment.source for segment in segments[index + 1 :] if segment.paragraph == paragraph]
    else:
        before = [segments[index - 1].source] if index > 0 else []
        after = [segments[index + 1].source] if index + 1 < len(segments) else []
    if before:
        parts.append("同じ段落の直前の原文:\n" + " ".join(before)[-600:])
    if after:
        parts.append("同じ段落の直後の原文:\n" + " ".join(after)[:600])
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
    return {
        "title": title,
        "generated_by": "PLaMo 3 Translate",
        "attribution": PLAMO_ATTRIBUTION,
        "model_license": PLAMO_LICENSE_URL,
        **metadata,
        "segments": [asdict(segment) for segment in segments],
    }


def bilingual_markdown(title: str, segments: Iterable[Segment]) -> str:
    lines = [
        f"# {title}",
        "",
        "> Built with PLaMo — PLaMo 3 Translate による機械翻訳です。",
        f"> [PLaMo Community License]({PLAMO_LICENSE_URL})",
        "",
    ]
    for segment in segments:
        if segment.kind == "heading":
            lines.extend([f"## {segment.source}", "", f"**訳:** {segment.translation or segment.source}", ""])
            continue
        position = f"P{segment.paragraph} / S{segment.sentence}" if segment.sentence else f"P{segment.paragraph}"
        for asset in segment.assets:
            lines.extend([f"![{segment.label or 'Figure'}]({asset})", ""])
        lines.extend(
            [
                f"### {segment.index + 1}. 原文（{position}）",
                "",
                segment.source,
                "",
                "### 訳文",
                "",
                segment.translation or f"⚠ {segment.error or '未翻訳'}",
                "",
            ]
        )
        if segment.note:
            lines.extend(["**メモ**", "", segment.note, ""])
    return "\n".join(lines)


def bilingual_html(
    title: str,
    segments: Iterable[Segment],
    asset_resolver: Callable[[str], str] | None = None,
) -> str:
    rows = []
    previous_paragraph = None
    for segment in segments:
        translation = segment.translation or f"⚠ {segment.error or '未翻訳'}"
        images = "".join(
            f'<figure><img src="{html.escape(asset_resolver(asset) if asset_resolver else asset)}" alt="Figure {segment.index + 1}"></figure>'
            for asset in segment.assets
        )
        if images:
            images = f'<div class="figure-gallery">{images}</div>'
        paragraph_start = bool(segment.paragraph and segment.paragraph != previous_paragraph)
        if segment.paragraph:
            position = f"P{segment.paragraph}<br>S{segment.sentence}" if segment.sentence else f"P{segment.paragraph}"
        else:
            position = "旧形式"
        rows.append(
            f'<section class="reader-row kind-{html.escape(segment.kind)}{" paragraph-start" if paragraph_start else ""}">'
            f'<div class="number"><b>{segment.index + 1}</b><span>{position}</span></div>'
            f'<article class="source">{images}<p>{html.escape(segment.source).replace(chr(10), "<br>")}</p></article>'
            f'<article class="translation"><p>{html.escape(translation).replace(chr(10), "<br>")}</p></article>'
            f'<aside class="note"><p>{html.escape(segment.note).replace(chr(10), "<br>") or "<span>メモなし</span>"}</p></aside>'
            "</section>"
        )
        previous_paragraph = segment.paragraph
    safe_title = html.escape(title)
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{safe_title} — 対訳</title>
<style>
*{{box-sizing:border-box}}body{{font-family:"Yu Gothic UI","Noto Sans JP",sans-serif;margin:0;background:#f3f1eb;color:#20231f}}
header{{padding:28px 32px;border-bottom:1px solid #d8d5ca;background:#fff;position:sticky;top:0;z-index:2}}h1{{font:600 1.45rem Georgia,"Yu Mincho",serif;margin:0}}header p{{margin:6px 0 0;font-size:.78rem;color:#687169}}header a,footer a{{color:#174d36}}
.labels,.reader-row{{display:grid;grid-template-columns:44px minmax(300px,1fr) minmax(300px,1fr) minmax(230px,.72fr);max-width:1800px;margin:auto}}
.labels{{position:sticky;top:89px;z-index:2;background:#174d36;color:#fff;font-size:.72rem;letter-spacing:.1em}}.labels>*{{padding:10px 18px;border-right:1px solid #376750}}
.reader-row{{background:#fff;border-bottom:1px solid #dedbd1;align-items:stretch}}.reader-row>*{{padding:10px 12px;border-right:1px solid #e1ded5;min-width:0}}
.number{{color:#728078;text-align:center;padding:22px 5px}}.number b,.number span{{display:block}}.number b{{font:italic .72rem Georgia,serif}}.number span{{font-size:.58rem;line-height:1.45;margin-top:7px}}p{{white-space:pre-wrap;line-height:1.78;margin:0;overflow-wrap:anywhere}}.source p{{font-family:Georgia,"Times New Roman","Yu Mincho",serif}}.translation{{background:#fbfcf9}}.note{{background:#f8f5ed;color:#5f675f;font-size:.86rem}}.note span{{color:#a0a49d}}
.paragraph-start{{border-top:3px solid #9aab9f;margin-top:8px}}
.kind-heading{{margin-top:24px;border-top:3px solid #174d36}}.kind-heading p{{font-weight:700;font-size:1.08rem}}.kind-equation p,.kind-table p{{font-family:"Cambria Math",Consolas,monospace;white-space:pre-wrap}}.figure-gallery{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px;margin-bottom:16px}}figure{{margin:0;background:#fff}}img{{display:block;width:100%;height:100%;max-height:460px;object-fit:contain;margin:auto;border:1px solid #ddd}}
footer{{padding:24px 32px;text-align:center;font-size:.78rem;color:#687169}}
@media(max-width:900px){{.labels,.reader-row{{grid-template-columns:36px minmax(260px,1fr) minmax(260px,1fr)}}.labels .memo-label{{display:none}}.note{{grid-column:2/4;border-top:1px dashed #d8d5ca}}}}
@media(max-width:620px){{header{{padding:20px 16px}}.labels{{display:none}}.reader-row{{grid-template-columns:32px 1fr}}.reader-row>*{{grid-column:2}}.number{{grid-column:1!important;grid-row:1/4}}}}
</style></head><body><header><h1>{safe_title}</h1><p>Built with PLaMo — PLaMo 3 Translate による機械翻訳です。 <a href="{PLAMO_LICENSE_URL}">PLaMo Community License</a></p></header><div class="labels"><span></span><b>ORIGINAL</b><b>TRANSLATION</b><b class="memo-label">MEMO</b></div><main>{''.join(rows)}</main><footer>Built with PLaMo · <a href="{PLAMO_LICENSE_URL}">PLaMo Community License</a></footer></body></html>"""
