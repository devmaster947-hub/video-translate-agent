import pytest
from video_translate.models import Segment
from video_translate.translation import projection, validate_translation


def source():
    return [Segment(id=1, start_ms=0, end_ms=1000, raw_text="原文", polished_text="润色原文")]


def test_local_translation_projection_and_validation():
    editable = projection(source())
    assert editable[0]["text"] == ""
    editable[0]["text"] = "Translated text"
    result = validate_translation(source(), editable)
    assert result[0].translated_text == "Translated text"


@pytest.mark.parametrize("change", [
    lambda item: item.update(id=2),
    lambda item: item.update(polished_text="changed"),
    lambda item: item.update(text=" "),
    lambda item: item.update(extra="field"),
])
def test_local_translation_rejects_invalid_edits(change):
    editable = projection(source())
    editable[0]["text"] = "Translated text"
    change(editable[0])
    with pytest.raises(ValueError):
        validate_translation(source(), editable)
