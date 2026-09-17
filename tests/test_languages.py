import pytest
from video_translate.languages import language, source_code


@pytest.mark.parametrize("code,whisper", [("zh-CN","zh"),
    ("zh-Hans","zh"),("zh-TW","zh"),("en-US","en")])
def test_provider_mapping(code, whisper):
    assert source_code(code) == whisper
    assert language(code).subtitle


def test_auto_source_only():
    assert source_code("auto") == "auto"
    with pytest.raises(ValueError): language("auto")
    with pytest.raises(ValueError): language("xx")
