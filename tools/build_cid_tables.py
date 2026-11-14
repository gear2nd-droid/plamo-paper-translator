"""Build the CID -> Unicode tables in cmaps/ from Adobe's pdf2unicode CMaps.

Japanese, Chinese and Korean fonts embedded with the Identity-H encoding
and no /ToUnicode map draw characters by CID. pypdf then has no way to
turn them into text (a LaTeX paper set in IPAMincho comes out as "͸ ͡ Ί ʹ"
instead of "はじめに"); PDF viewers use Adobe's public tables instead.
This script turns those tables into small range lists.

Source: https://github.com/adobe-type-tools/mapping-resources-pdf
        pdf2unicode/Adobe-{Japan1,GB1,CNS1,Korea1}-UCS2 (BSD-3-Clause)

    python tools/build_cid_tables.py path/to/mapping-resources-pdf/pdf2unicode
"""

from __future__ import annotations

import gzip
import json
import re
import sys
from pathlib import Path

ORDERINGS = ("Japan1", "GB1", "CNS1", "Korea1")


def utf16(hex_value: str) -> str:
    return bytes.fromhex(hex_value).decode("utf-16-be")


def parse_cmap(text: str) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for block in re.findall(r"beginbfchar(.*?)endbfchar", text, re.S):
        for cid, value in re.findall(r"<([0-9a-fA-F]+)>\s*<([0-9a-fA-F]+)>", block):
            mapping[int(cid, 16)] = utf16(value)
    for block in re.findall(r"beginbfrange(.*?)endbfrange", text, re.S):
        for low, high, target in re.findall(r"<([0-9a-fA-F]+)>\s*<([0-9a-fA-F]+)>\s*(<[0-9a-fA-F]+>|\[[^\]]*\])", block):
            first, last = int(low, 16), int(high, 16)
            if target.startswith("["):
                for offset, value in enumerate(re.findall(r"<([0-9a-fA-F]+)>", target)):
                    mapping[first + offset] = utf16(value)
                continue
            start = utf16(target[1:-1])
            for offset in range(last - first + 1):
                mapping[first + offset] = start[:-1] + chr(ord(start[-1]) + offset)
    return mapping


def ranges(mapping: dict[int, str]) -> list[list[object]]:
    """[first CID, text, count]: count > 1 for runs of consecutive single code points."""
    result: list[list[object]] = []
    for cid in sorted(mapping):
        text = mapping[cid]
        if text == "�":
            continue
        if result and len(text) == 1 and len(result[-1][1]) == 1:
            first, start, count = result[-1]
            if first + count == cid and ord(start) + count == ord(text):
                result[-1][2] = count + 1
                continue
        result.append([cid, text, 1])
    return result


def main() -> None:
    source = Path(sys.argv[1])
    target = Path(__file__).resolve().parents[1] / "cmaps"
    target.mkdir(exist_ok=True)
    for ordering in ORDERINGS:
        mapping = parse_cmap((source / f"Adobe-{ordering}-UCS2").read_text(encoding="latin-1"))
        payload = {"source": f"adobe-type-tools/mapping-resources-pdf pdf2unicode/Adobe-{ordering}-UCS2", "ranges": ranges(mapping)}
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        (target / f"Adobe-{ordering}.json.gz").write_bytes(gzip.compress(data, compresslevel=9, mtime=0))
        print(f"{ordering}: {len(mapping)} CIDs, {len(payload['ranges'])} ranges")


if __name__ == "__main__":
    main()
