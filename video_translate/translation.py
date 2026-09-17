"""Agent-local translation projection and deterministic structural validation."""
from .models import Segment


FIELDS = {"id", "start_ms", "end_ms", "raw_text", "polished_text", "text"}


def projection(segments: list[Segment]) -> list[dict]:
    result = []
    for item in segments:
        if not item.polished_text:
            raise ValueError("Translation source is not polished")
        result.append({"id": item.id, "start_ms": item.start_ms, "end_ms": item.end_ms,
                       "raw_text": item.raw_text, "polished_text": item.polished_text,
                       "text": ""})
    return result


def validate_translation(source: list[Segment], edited: list[dict]) -> list[Segment]:
    if not isinstance(edited, list) or not source or len(source) != len(edited):
        raise ValueError("Translation count mismatch")
    expected = projection(source)
    result = []
    for item, frozen, candidate in zip(source, expected, edited):
        if not isinstance(candidate, dict) or set(candidate) != FIELDS:
            raise ValueError("Translation field mismatch")
        for key in FIELDS - {"text"}:
            if type(candidate[key]) is not type(frozen[key]) or candidate[key] != frozen[key]:
                raise ValueError("Translation modified a frozen field")
        text = candidate["text"]
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Empty translated text")
        result.append(type(item).model_validate(
            item.model_dump() | {"translated_text": text.strip()}))
    return result
