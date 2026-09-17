from types import SimpleNamespace as NS
import pytest
from video_translate.asr import transcribe, ASRError
from video_translate.config import ASRConfig
from video_translate.resegment import resegment


def test_lone_short_and_missing_words():
    for duration in (0.2, 12):
        result = resegment([{"start":0,"end":duration,"text":"你好","words":[]}], "zh")
        assert len(result) == 1 and result[0].raw_text == "你好"


def test_word_split_preserves_text():
    words = [{"word":f"word{i}","start":i,"end":i+0.8} for i in range(10)]
    result = resegment([{"start":0,"end":10,"text":"long","words":words}], "en", 1000, 3000)
    assert " ".join(s.raw_text for s in result) == " ".join(w["word"] for w in words)
    assert len(result) > 1


def test_whitespace_words_never_drop_a_later_segment():
    result=resegment([{"start":0,"end":1,"text":"first","words":[]},
        {"start":2,"end":12,"text":"keep this","words":[{"word":" ","start":2,"end":12}]}],"en")
    assert [s.raw_text for s in result]==["first","keep this"]


def test_asr_options_and_lazy_failure(tmp_path):
    observed = {}
    class Model:
        def __init__(self, *args, **kwargs): observed.update(kwargs)
        def transcribe(self, *args, **kwargs):
            observed.update(kwargs)
            return iter([NS(text="hello",start=0,end=.5,words=None)]), NS(language="en")
    result, language, runtime = transcribe(tmp_path / "audio.wav", "auto", ASRConfig(device="cpu"), Model)
    assert result[0].raw_text == "hello" and language == "en"
    assert observed["language"] is None and observed["compute_type"] == "int8"
    assert observed["vad_filter"] and observed["word_timestamps"] and observed["beam_size"] == 5
    class Broken(Model):
        def transcribe(self, *args, **kwargs):
            def stream():
                raise RuntimeError("private failure text")
                yield
            return stream(), NS(language="en")
    with pytest.raises(ASRError, match="ASR_EXECUTION_FAILED"):
        transcribe(tmp_path / "a.wav", "en", ASRConfig(device="cpu"), Broken)
