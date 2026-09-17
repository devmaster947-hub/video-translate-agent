from video_translate.config import SubtitleConfig
from video_translate.models import Segment
import re

from video_translate.subtitles import _background_geometry, ass


def test_layout_uses_one_translucent_box_for_whole_text_block():
    segment = Segment(id=1, start_ms=0, end_ms=1000, raw_text="source",
                      translated_text="translated subtitle", final_start_ms=3000, final_end_ms=4500)
    layout = {"version": 1, "default": {"center_x": .5, "bottom_y": .86,
              "width": .6, "height": .08}, "segments": {"1": {"center_x": .4,
              "bottom_y": .5, "width": .5, "height": .08}}}
    output = ass([segment], width=1000, height=720, layout=layout)
    assert "0:00:03.00,0:00:04.50" in output
    assert "\\pos(400,360)" in output
    assert "\\1a&HB0&" in output and "BorderStyle, Outline" in output
    assert output.count("Dialogue:") == 2
    assert output.count("\\p1") == 1
    assert "\\fs22" in output


def test_two_lines_share_one_background_rectangle():
    segment = Segment(id=1, start_ms=0, end_ms=1000, raw_text="source",
                      translated_text="first line\nsecond line", final_start_ms=0, final_end_ms=1000)
    output = ass([segment], width=1000, height=720)
    assert output.count("\\p1") == 1
    assert output.count("Dialogue:") == 2
    assert "first line\\Nsecond line" in output


def test_missing_segment_uses_dominant_layout():
    segment = Segment(id=2, start_ms=0, end_ms=1000, raw_text="source",
                      translated_text="text", final_start_ms=0, final_end_ms=1000)
    layout = {"version": 1, "default": {"center_x": .55, "bottom_y": .8,
              "width": .5, "height": .08}, "segments": {}}
    assert "\\pos(550,576)" in ass([segment], width=1000, height=720, layout=layout)


def test_font_size_is_stable_and_background_stays_inside_frame():
    segments = [
        Segment(id=1, start_ms=0, end_ms=1000, raw_text="source",
                translated_text="Short subtitle", final_start_ms=0, final_end_ms=1000),
        Segment(id=2, start_ms=1000, end_ms=2000, raw_text="source",
                translated_text="This longer subtitle should be balanced cleanly across two lines",
                final_start_ms=1000, final_end_ms=2000),
    ]
    layout = {"version": 1, "default": {"center_x": .5, "bottom_y": .86,
              "width": .3, "height": .08}, "segments": {
                  "1": {"center_x": .02, "bottom_y": .5, "width": .2, "height": .08},
                  "2": {"center_x": .98, "bottom_y": .5, "width": .9, "height": .08},
              }}
    output = ass(segments, width=720, height=1280, layout=layout)
    assert set(re.findall(r"\\fs(\d+)", output)) == {"40"}
    rectangles = re.findall(r"\\p1[^}]*}m \d+ 0 l \d+ 0 b (\d+) 0", output)
    assert rectangles and all(int(right) <= round(720 * .88) for right in rectangles)
    positions = [tuple(map(int, match)) for match in re.findall(r"\\pos\((\d+),(\d+)\)\\p1", output)]
    assert positions
    for (x, _), right in zip(positions, map(int, rectangles)):
        assert x - right / 2 >= 0
        assert x + right / 2 <= 720


def test_english_background_tightly_hugs_balanced_text():
    segment = Segment(id=1, start_ms=0, end_ms=1000, raw_text="source",
                      translated_text="Mooekiss's sold-out spring shade is back!",
                      final_start_ms=0, final_end_ms=1000)
    output = ass([segment], config=SubtitleConfig(font_size=28), width=720, height=1280)
    width = int(re.search(r"\\p1[^}]*}m \d+ 0 l \d+ 0 b (\d+) 0", output).group(1))
    assert width < 500
    assert "\\N" in output


def test_two_line_background_has_no_extra_vertical_leading():
    _, height, _, padding_y = _background_geometry(
        ["Mooekiss's sold-out", "spring shade is back!"], 50, 634)
    assert height == 102
    assert padding_y == 3
