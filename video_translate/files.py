"""Atomic stage artifacts and canonical segment serialization."""
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from .models import Segment


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False,
                                prefix=".json-", suffix=".tmp") as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def save_segments(path: Path, segments: list[Segment]):
    write_json(path, [segment.model_dump(mode="json") for segment in segments])


def load_segments(path: Path) -> list[Segment]:
    result = [Segment.model_validate(item) for item in read_json(path)]
    if not result or len({item.id for item in result}) != len(result):
        raise ValueError("Invalid segment collection")
    if any(a.start_ms >= b.start_ms for a, b in zip(result, result[1:])):
        raise ValueError("Segment starts must increase")
    return result
