"""Agent edits text; Python validates structure, never calls a language model."""
from .models import Segment


FIELDS = {"id", "start_ms", "end_ms", "raw_text", "text"}


def projection(segments: list[Segment]) -> list[dict]:
    return [{"id": item.id, "start_ms": item.start_ms, "end_ms": item.end_ms,
             "raw_text": item.raw_text, "text": item.raw_text} for item in segments]


def validate_polish(raw: list[dict], polished: list[dict]) -> list[Segment]:
    if not isinstance(raw, list) or not isinstance(polished, list) or not raw or len(raw) != len(polished):
        raise ValueError("Polish count mismatch")
    result = []
    for original, edited in zip(raw, polished):
        if not isinstance(original, dict) or not isinstance(edited, dict) or set(original) != FIELDS or set(edited) != FIELDS:
            raise ValueError("Polish field mismatch")
        for key in FIELDS - {"text"}:
            if type(original[key]) is not type(edited[key]) or original[key] != edited[key]:
                raise ValueError("Polish modified a frozen field")
        if not isinstance(edited["text"], str) or not edited["text"].strip():
            raise ValueError("Empty polished text")
        result.append(Segment(id=original["id"], start_ms=original["start_ms"], end_ms=original["end_ms"],
                              raw_text=original["raw_text"], polished_text=edited["text"]))
    if len({item.id for item in result}) != len(result):
        raise ValueError("Duplicate segment ID")
    if any(a.start_ms >= b.start_ms for a, b in zip(result, result[1:])):
        raise ValueError("Invalid segment order")
    return result
