"""Word/pause segmentation adapted from pyVideoTrans _stt_utils.py.

Reference: ad0b8bd8ec8a3cdceaab4358fd7fdc1ea19b3800, GPL-3.0.
Uses its punctuation/pause principles, without GUI/config/logging dependencies.
"""
import re
from .models import Segment


def resegment(raws: list[dict], language: str, minimum=1000, maximum=6000) -> list[Segment]:
    no_space = language in {"zh", "ja", "ko", "th", "yue", "km"}
    chunks = []
    for raw in raws:
        start, end = round(raw["start"] * 1000), round(raw["end"] * 1000)
        text = raw["text"].strip()
        if not text:
            continue
        words = raw.get("words") or []
        if end - start <= maximum or not words:
            chunks.append((start, end, text))
            continue
        current = []
        before = len(chunks)
        for word in words:
            if not word["word"].strip():
                continue
            if current:
                duration = (current[-1]["end"] - current[0]["start"]) * 1000
                gap = (word["start"] - current[-1]["end"]) * 1000
                if ((word["end"] - current[0]["start"]) * 1000 > maximum
                        or duration >= minimum and (gap >= 400 or current[-1]["word"].rstrip().endswith(tuple(".?!。？！")))):
                    chunks.append(_chunk(current, no_space))
                    current = []
            current.append(word)
        if current:
            chunks.append(_chunk(current, no_space))
        elif len(chunks) == before:
            chunks.append((start, end, text))
    merged = []
    for start, end, text in chunks:
        # Merge short trailing segments only before raw publication; never lose a lone short phrase.
        if merged and (end - start < minimum or start <= merged[-1][0]):
            a, b, previous = merged.pop()
            merged.append((a, max(b, end), previous + ("" if no_space else " ") + text))
        else:
            merged.append((start, end, text))
    return [Segment(id=i + 1, start_ms=start, end_ms=end, raw_text=text)
            for i, (start, end, text) in enumerate(merged)]


def _chunk(words, no_space):
    text = ("" if no_space else " ").join(word["word"].strip() for word in words)
    text = re.sub(r"\s+([.,?!:;])", r"\1", text)
    return round(words[0]["start"] * 1000), round(words[-1]["end"] * 1000), text
