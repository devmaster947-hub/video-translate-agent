import pytest
from video_translate.models import Segment
from video_translate.polish import projection, validate_polish


def raw():
    return projection([Segment(id=1, start_ms=0, end_ms=900, raw_text="hello"),
                       Segment(id=2, start_ms=1000, end_ms=2000, raw_text="world")])


def test_polish_only_text_changes():
    edited = raw()
    edited[0]["text"] = "Hello."
    assert validate_polish(raw(), edited)[0].polished_text == "Hello."


@pytest.mark.parametrize("field,value", [("id",2), ("start_ms",1), ("end_ms",1000),
    ("raw_text","changed"), ("extra",1), ("id",True), ("text","")])
def test_frozen_fields(field, value):
    edited = raw()
    edited[0][field] = value
    with pytest.raises(ValueError):
        validate_polish(raw(), edited)


def test_count_order_and_missing_fields():
    for edited in (raw()[:1], list(reversed(raw())), [{"text":"x"}] * 2):
        with pytest.raises(ValueError):
            validate_polish(raw(), edited)
