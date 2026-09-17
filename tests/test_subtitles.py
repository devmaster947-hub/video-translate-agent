import pytest
from video_translate.models import Segment
from video_translate.subtitles import srt, ass, timestamp


def segment():
    return Segment(id=1,start_ms=0,end_ms=1000,raw_text="original",translated_text="你好\n{test}\\N",
                   final_start_ms=3000,final_end_ms=4250)


def test_final_timestamps_only():
    assert "00:00:03,000 --> 00:00:04,250" in srt([segment()])
    text=ass([segment()])
    assert "0:00:03.00,0:00:04.25" in text and "你好\\N｛test｝＼N" in text
    assert text.count("\\p1") == 1 and text.count("Dialogue:") == 2
    assert timestamp(3600123)=="01:00:00,123"


def test_no_original_timeline_fallback():
    with pytest.raises(ValueError):
        srt([Segment(id=1,start_ms=0,end_ms=1000,raw_text="x",translated_text="text")])
