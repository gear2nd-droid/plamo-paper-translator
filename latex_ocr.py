"""Equation images to LaTeX with a local model.

The models are the ONNX export of LaTeX-OCR (pix2tex, MIT License) published
by RapidLaTeXOCR (MIT License). This module ports their inference to
onnxruntime, Pillow and NumPy; the models are downloaded on request and never
shipped with the application.
"""

from __future__ import annotations

import json
import os
import re
import threading
import urllib.request
from pathlib import Path
from typing import Callable

MODEL_BASE_URL = "https://github.com/RapidAI/RapidLaTeXOCR/releases/download/v0.0.0"
# File name -> minimum size in bytes, to catch a broken download.
MODEL_FILES = {
    "image_resizer.onnx": 30_000_000,
    "encoder.onnx": 70_000_000,
    "decoder.onnx": 40_000_000,
    "tokenizer.json": 10_000,
}
# Published sizes, for the overall download progress.
MODEL_SIZES = {
    "image_resizer.onnx": 38_967_751,
    "encoder.onnx": 89_008_136,
    "decoder.onnx": 50_952_726,
    "tokenizer.json": 24_174,
}
MAX_DIMS = (672, 192)
MIN_DIMS = (32, 32)
BOS_TOKEN = 1
EOS_TOKEN = 2
MAX_SEQ_LEN = 512
MEAN = 0.7931
STD = 0.1738


def models_present(model_dir: Path) -> bool:
    return all((model_dir / name).is_file() and (model_dir / name).stat().st_size >= size for name, size in MODEL_FILES.items())


def runtime_available() -> bool:
    try:
        import numpy  # noqa: F401
        import onnxruntime  # noqa: F401
    except Exception:
        return False
    return True


def download_models(model_dir: Path, progress: Callable[[str, int, int], None] | None = None) -> None:
    """Download the model files that are missing, each through a partial file."""
    model_dir.mkdir(parents=True, exist_ok=True)
    for name, minimum in MODEL_FILES.items():
        target = model_dir / name
        if target.is_file() and target.stat().st_size >= minimum:
            continue
        partial = target.with_name(name + ".partial")
        request = urllib.request.Request(f"{MODEL_BASE_URL}/{name}", headers={"User-Agent": "PLaMo-Paper-Translator"})
        with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as output:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                output.write(chunk)
                done += len(chunk)
                if progress:
                    progress(name, done, total)
        if partial.stat().st_size < minimum:
            partial.unlink(missing_ok=True)
            raise RuntimeError(f"{name} のダウンロードが不完全です。")
        os.replace(partial, target)


def _byte_decoder() -> dict[str, int]:
    """The inverse of the GPT-2 byte-level alphabet used by the tokenizer."""
    printable = list(range(ord("!"), ord("~") + 1)) + list(range(ord("¡"), ord("¬") + 1)) + list(range(ord("®"), ord("ÿ") + 1))
    codes = printable[:]
    extra = 0
    for value in range(256):
        if value not in printable:
            printable.append(value)
            codes.append(256 + extra)
            extra += 1
    return {chr(code): value for value, code in zip(printable, codes)}


def _repeating(ids: list[int], rounds: int = 6) -> bool:
    for size in range(1, 9):
        if len(ids) >= size * rounds and all(ids[-size * r - size : len(ids) - size * r] == ids[-size:] for r in range(1, rounds)):
            return True
    return False


def _trim_repetition(ids: list[int]) -> list[int]:
    for size in range(1, 9):
        cycle = ids[-size:]
        end = len(ids)
        while end - size >= 0 and ids[end - size : end] == cycle:
            end -= size
        if len(ids) - end >= size * 6:
            return ids[: end + size]
    return ids


class LatexOCR:
    def __init__(self, model_dir: Path, threads: int = 0):
        import numpy as np
        import onnxruntime as ort

        self.np = np
        options = ort.SessionOptions()
        options.log_severity_level = 4
        options.enable_cpu_mem_arena = False
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        if threads:
            options.intra_op_num_threads = threads

        def session(name: str):
            return ort.InferenceSession(str(model_dir / name), sess_options=options, providers=["CPUExecutionProvider"])

        self.resizer = session("image_resizer.onnx")
        self.encoder = session("encoder.onnx")
        self.decoder = session("decoder.onnx")
        vocabulary = json.loads((model_dir / "tokenizer.json").read_text(encoding="utf-8"))
        self.tokens = {index: token for token, index in vocabulary["model"]["vocab"].items()}
        self.special = {item["id"] for item in vocabulary.get("added_tokens", []) if item.get("special")}
        self.bytes = _byte_decoder()
        self.lock = threading.Lock()

    # Preprocessing (pix2tex) ------------------------------------------------
    def _pad(self, image):
        """Crop to the ink and pad to a multiple of 32, dark text on white."""
        np = self.np
        from PIL import Image

        data = np.asarray(image.convert("L"), dtype=np.float32)
        low, high = float(data.min()), float(data.max())
        if high - low < 1:
            raise ValueError("empty image")
        data = (data - low) / (high - low) * 255
        if data.mean() > 128:
            ink = data < 128
        else:
            ink = data > 128
            data = 255 - data
        rows = np.flatnonzero(ink.any(axis=1))
        cols = np.flatnonzero(ink.any(axis=0))
        if not len(rows) or not len(cols):
            raise ValueError("empty image")
        crop = data[rows[0] : rows[-1] + 1, cols[0] : cols[-1] + 1]
        height, width = crop.shape
        padded = Image.new("L", (-(-width // 32) * 32, -(-height // 32) * 32), 255)
        padded.paste(Image.fromarray(crop.astype(np.uint8), "L"), (0, 0))
        return padded

    def _minmax(self, image):
        from PIL import Image

        ratios = [size / limit for size, limit in zip(image.size, MAX_DIMS)]
        if max(ratios) > 1:
            size = tuple(max(1, int(value // max(ratios))) for value in image.size)
            image = image.resize(size, Image.BILINEAR)
        size = tuple(max(value, minimum) for value, minimum in zip(image.size, MIN_DIMS))
        if size != image.size:
            padded = Image.new("L", size, 255)
            padded.paste(image, image.getbbox())
            image = padded
        return image

    def _tensor(self, image):
        np = self.np
        data = np.asarray(image.convert("L"), dtype=np.float32)
        data = (data - MEAN * 255) / (STD * 255)
        return data[None, None, :, :].astype(np.float32)

    def _resize(self, image):
        """Let the resizer model choose the width the formula was trained at."""
        from PIL import Image

        np = self.np
        start = self._minmax(self._pad(image)).convert("RGB")
        ratio, width, height = 1.0, start.size[0], start.size[1]
        tensor = None
        for _ in range(10):
            height = int(height * ratio)
            method = Image.BILINEAR if ratio > 1 else Image.LANCZOS
            padded = self._pad(self._minmax(start.resize((width, height), method)))
            tensor = self._tensor(padded)
            scores = self.resizer.run(None, {self.resizer.get_inputs()[0].name: tensor})[0]
            width = (int(np.argmax(scores, axis=-1).reshape(-1)[0]) + 1) * 32
            if width == padded.size[0]:
                break
            ratio = width / padded.size[0]
        return tensor

    # Decoding ------------------------------------------------------------------
    def _decode(self, tensor) -> list[int]:
        np = self.np
        context = self.encoder.run(None, {self.encoder.get_inputs()[0].name: tensor})[0]
        names = [item.name for item in self.decoder.get_inputs()]
        out = np.array([[BOS_TOKEN]], dtype=np.int64)
        for _ in range(MAX_SEQ_LEN):
            x = out[:, -MAX_SEQ_LEN:]
            mask = np.ones_like(x, dtype=bool)
            logits = self.decoder.run(None, dict(zip(names, (x, mask, context))))[0][:, -1, :]
            token = int(np.argmax(logits, axis=-1)[0])
            out = np.concatenate([out, np.array([[token]], dtype=np.int64)], axis=-1)
            if token == EOS_TOKEN:
                break
            ids = out[0, 1:].tolist()
            if _repeating(ids):
                # A loop ("\qquad\qquad..."): keep one round of it.
                return _trim_repetition(ids)
        return [int(value) for value in out[0, 1:]]

    def _detokenize(self, ids: list[int]) -> str:
        text = "".join(self.tokens.get(index, "") for index in ids if index not in self.special)
        raw = bytes(self.bytes[char] for char in text if char in self.bytes)
        return raw.decode("utf-8", errors="replace").strip()

    @staticmethod
    def post_process(text: str) -> str:
        """Remove the spaces that LaTeX ignores (pix2tex's post-processing)."""
        text_reg = r"(\\(operatorname|mathrm|text|mathbf)\s?\*? {.*?})"
        letter = "[a-zA-Z]"
        noletter = r"[\W_^\d]"
        names = [match[0].replace(" ", "") for match in re.findall(text_reg, text)]
        text = re.sub(text_reg, lambda match: str(names.pop(0)), text)
        news = text
        while True:
            text = news
            news = re.sub(r"(?!\\ )(%s)\s+?(%s)" % (noletter, noletter), r"\1\2", text)
            news = re.sub(r"(?!\\ )(%s)\s+?(%s)" % (noletter, letter), r"\1\2", news)
            news = re.sub(r"(%s)\s+?(%s)" % (letter, noletter), r"\1\2", news)
            if news == text:
                break
        return text

    def _drop_number(self, image, numbered: bool = False):
        """Cut off an equation number at the right, after a wide blank gap.

        The number is not part of the formula to copy, and the model reads
        the formula better without it.
        """
        np = self.np
        ink = np.asarray(image.convert("L"), dtype=np.uint8) < 160
        columns = np.flatnonzero(ink.any(axis=0))
        rows = np.flatnonzero(ink.any(axis=1))
        if len(columns) < 2 or not len(rows):
            return image
        gaps = np.diff(columns)
        widest = int(np.argmax(gaps))
        gap = int(gaps[widest])
        right = columns[widest + 1 :]
        tail = int(right[-1] - right[0] + 1)
        tail_rows = np.flatnonzero(ink[:, right[0] : right[-1] + 1].any(axis=1))
        height = int(tail_rows[-1] - tail_rows[0] + 1)  # the number's own height
        left_width = int(columns[widest] - columns[0] + 1)
        if gap >= max(1.5 * height, 20) and tail <= 4.0 * height and left_width > 2 * tail:
            return image.crop((0, 0, int(columns[widest]) + 8, image.height))
        if numbered:
            # The row is known to end in "(N)": take the last clear gap before
            # a narrow group at the right, even when the formula fills the line.
            # The closing parenthesis gives the height of the number.
            last = columns[-1]
            start = len(columns) - 1
            while start > 0 and gaps[start - 1] <= 2:
                start -= 1
            paren_rows = np.flatnonzero(ink[:, columns[start] : last + 1].any(axis=1))
            height = int(paren_rows[-1] - paren_rows[0] + 1)
            for position in range(len(gaps) - 1, -1, -1):
                right = columns[position + 1 :]
                tail = int(right[-1] - right[0] + 1)
                if tail > 3.5 * height:
                    break
                if gaps[position] >= max(0.6 * height, 8) and tail >= 0.8 * height:
                    return image.crop((0, 0, int(columns[position]) + 8, image.height))
        return image

    def __call__(self, image, numbered: bool = False) -> str:
        """Image (PIL or path) -> LaTeX."""
        from PIL import Image

        if not hasattr(image, "convert"):
            with Image.open(image) as opened:
                image = opened.convert("RGBA")
        if image.mode in {"RGBA", "LA", "P"}:
            image = image.convert("RGBA")
            background = Image.new("RGBA", image.size, "white")
            image = Image.alpha_composite(background, image)
        image = self._drop_number(image.convert("RGB"), numbered)
        with self.lock:
            tensor = self._resize(image)
            ids = self._decode(tensor)
        return self.post_process(self._detokenize(ids))


_engines: dict[str, LatexOCR] = {}
_engine_lock = threading.Lock()


def engine(model_dir: Path, threads: int = 0) -> LatexOCR:
    key = str(model_dir.resolve())
    with _engine_lock:
        if key not in _engines:
            _engines[key] = LatexOCR(model_dir, threads)
        return _engines[key]


def strip_equation_number(tex: str) -> str:
    """Drop a trailing equation number such as "\\qquad(3)" that was in the image."""
    value = tex.strip()
    # Spacing before the number is required, so "f(1)" at the end stays.
    spacing = r"(?:\\[qy]?quad|\\[ ,;:!]|~|\\hfill)"
    number = r"\(\s*(?:\\mathrm\{)?\s*\d+(?:\.\d+)?[a-z]?\s*\}?\s*\)"
    pattern = rf"(?:{spacing}\s*)+(?:\\(?:mathrm|text)\s*\{{\s*{number}\s*\}}|{number})\s*\.?$|\\(?:mathrm|text)\s*\{{\s*{number}\s*\}}\s*\.?$"
    stripped = re.sub(pattern, "", value)
    return stripped.rstrip(" ~") if stripped and stripped != value else value
