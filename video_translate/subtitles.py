"""SRT and ASS serializers consume final timestamps exclusively."""
import math
from pathlib import Path
from .config import SubtitleConfig


def timestamp(ms, ass=False):
    units=ms//10 if ass else ms
    scale=100 if ass else 1000
    seconds,fraction=divmod(units,scale)
    minutes,seconds=divmod(seconds,60)
    hours,minutes=divmod(minutes,60)
    return (f"{hours}:{minutes:02}:{seconds:02}.{fraction:02}" if ass
            else f"{hours:02}:{minutes:02}:{seconds:02},{fraction:03}")


def _validate(segments):
    if not segments:
        raise ValueError("No subtitles")
    for s in segments:
        if s.final_start_ms is None or s.final_end_ms is None or not s.translated_text:
            raise ValueError("Final timeline and translation required")


def srt(segments):
    _validate(segments)
    return "\n\n".join(f"{i+1}\n{timestamp(s.final_start_ms)} --> {timestamp(s.final_end_ms)}\n{s.translated_text}"
                        for i,s in enumerate(segments))+"\n"


def ass_text(text):
    # Display-only fullwidth equivalents prevent user text becoming ASS control syntax.
    # Original translated text remains unchanged in Segment and SRT.
    return text.replace("\\","＼").replace("{","｛").replace("}","｝").replace("\r\n","\n").replace("\r","\n").replace("\n",r"\N")


def _display_width(text):
    def units(character):
        if ord(character) > 255:
            return 1.0
        if character.isspace():
            return 0.25
        if character in "ilI.,'!:;|`":
            return 0.20
        if character in "MW@#%&":
            return 0.68
        if character in "-_()[]{}":
            return 0.30
        return 0.42
    return sum(units(character) for character in text)


def _balanced_lines(text, max_units):
    """Reflow text to at most two lines without changing the video-wide font size."""
    explicit_lines = [line.strip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
                      if line.strip()]
    if 1 < len(explicit_lines) <= 2 and all(
            _display_width(line) <= max_units for line in explicit_lines):
        return explicit_lines
    normalized = " ".join(text.split())
    if not normalized or _display_width(normalized) <= max_units:
        return [normalized]
    words = normalized.split(" ")
    if len(words) > 1:
        candidates = [(" ".join(words[:index]), " ".join(words[index:]))
                      for index in range(1, len(words))]
    else:
        candidates = [(normalized[:index], normalized[index:])
                      for index in range(1, len(normalized))]
    return list(min(candidates, key=lambda pair: (
        max(_display_width(pair[0]), _display_width(pair[1])),
        abs(_display_width(pair[0]) - _display_width(pair[1])),
    )))


def _positioned_text(text, config, width, height):
    # A single configured size is used for every cue; only video resolution scales it.
    font_size = max(1, round(config.font_size * height / 720))
    safe_width = max(1, width * config.max_width_ratio)
    padding_x = max(1, round(font_size * 0.18), round(6 * min(1, font_size / 28)))
    max_units = max(1, (safe_width - 2 * padding_x) / (font_size * 1.06))
    lines = _balanced_lines(text, max_units)
    return r"\N".join(ass_text(line) for line in lines), font_size, lines


def _rounded_rectangle(width, height, radius):
    """One ASS vector path for the whole subtitle block, including both lines."""
    w, h, r = round(width), round(height), round(radius)
    return (f"m {r} 0 l {w-r} 0 b {w} 0 {w} 0 {w} {r} "
            f"l {w} {h-r} b {w} {h} {w} {h} {w-r} {h} "
            f"l {r} {h} b 0 {h} 0 {h} 0 {h-r} "
            f"l 0 {r} b 0 0 0 0 {r} 0")


def _background_geometry(lines, font_size, max_width):
    padding_x = max(1, round(font_size * 0.18), round(6 * min(1, font_size / 28)))
    padding_y = max(1, round(font_size * 0.06), round(2 * min(1, font_size / 28)))
    text_width = max((_display_width(line) for line in lines), default=1) * font_size * 1.06
    width = min(round(max_width), math.ceil(text_width + 2 * padding_x))
    # Libass glyphs occupy less than the nominal line box. Keep the vector
    # backdrop close to the visible outline instead of adding leading above it.
    height = math.ceil(len(lines) * font_size * 0.96 + 2 * padding_y)
    radius = min(round(font_size * 0.18), width // 2, height // 2)
    return width, height, radius, padding_y


def _layout_for(segment, layout):
    if not layout:
        return None
    return layout.get("segments", {}).get(str(segment.id)) or layout.get("default")


def ass(segments, config=SubtitleConfig(), width=1920, height=1080, layout=None):
    _validate(segments)
    style_font_size = round(config.font_size)
    header=f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{config.font},{style_font_size},&H00FFFFFF,&H00FFFFFF,&H50000000,&H00000000,0,0,0,0,100,100,0,0,1,1.2,0,{config.alignment},20,20,{config.margin_v},1
Style: Backdrop,Arial,1,&H00101820,&H00101820,&H00101820,&H00101820,0,0,0,0,100,100,0,0,1,0,0,2,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    for segment in segments:
        box = _layout_for(segment, layout) or {
            "center_x": 0.5,
            "bottom_y": max(0, min(1, (height - config.margin_v) / height)),
            "width": max(0.1, (width - 40) / width),
            "height": 0.1,
        }
        start, end = timestamp(segment.final_start_ms, True), timestamp(segment.final_end_ms, True)
        x, bottom = round(box["center_x"] * width), round(box["bottom_y"] * height)
        text, font_size, lines = _positioned_text(segment.translated_text, config, width, height)
        max_width = width * config.max_width_ratio
        box_width, box_height, radius, padding_y = _background_geometry(
            lines, font_size, max_width)
        half_width = box_width / 2
        x = round(max(half_width, min(width - half_width, x)))
        bottom = min(height, max(box_height, bottom))
        alpha = f"{config.background_alpha:02X}"
        drawing = _rounded_rectangle(box_width, box_height, radius)
        events.append(
            f"Dialogue: 0,{start},{end},Backdrop,,0,0,0,,"
            f"{{\\an2\\pos({x},{bottom})\\p1\\bord0\\shad0\\1c&H101820&\\1a&H{alpha}&}}{drawing}"
        )
        events.append(
            f"Dialogue: 1,{start},{end},Default,,0,0,0,,"
            f"{{\\an2\\pos({x},{bottom-padding_y})\\fs{font_size}}}{text}"
        )
    return header+"\n".join(events)+"\n"


def write_subtitles(directory: Path, segments, config, width, height, layout=None):
    (directory/"target.srt").write_text(srt(segments),encoding="utf-8")
    (directory/"target.ass").write_text(ass(segments,config,width,height,layout=layout),encoding="utf-8")
