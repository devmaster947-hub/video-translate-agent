import subprocess

import pytest

from video_translate.config import MediaConfig
from video_translate.media import Media, MediaError


def test_subprocess_arguments_and_windows_paths(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr("video_translate.media.executable", lambda value: "C:/tools/ffprobe.exe")

    def run(args, **kwargs):
        captured.update(args=args, **kwargs)
        return subprocess.CompletedProcess(args, 0, stdout="result")

    monkeypatch.setattr("video_translate.media.subprocess.run", run)
    assert Media(MediaConfig()).run("ffprobe", ["-i", "D:\\中文 空格\\片段.mp4"], cwd=tmp_path) == "result"
    assert captured["args"][-1] == "D:\\中文 空格\\片段.mp4"
    assert captured["shell"] is False and captured["check"] is True
    assert captured["timeout"] > 0 and captured["cwd"] == str(tmp_path)


@pytest.mark.parametrize("error,code", [
    (subprocess.TimeoutExpired("tool", 1), "MEDIA_TIMEOUT"),
    (subprocess.CalledProcessError(1, "tool", stderr="private value"), "MEDIA_EXECUTION_FAILED")])
def test_process_errors_do_not_expose_raw_stderr(monkeypatch, error, code):
    monkeypatch.setattr("video_translate.media.executable", lambda value: value)

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr("video_translate.media.subprocess.run", fail)
    with pytest.raises(MediaError, match=code):
        Media(MediaConfig()).run("ffmpeg", [])


@pytest.mark.parametrize("raw", ["not json", "{}", '{"streams":[],"format":{"duration":"1"}}',
    '{"streams":[{}],"format":{"duration":"NaN"}}'])
def test_invalid_probe_metadata(monkeypatch, tmp_path, raw):
    source = tmp_path / "bad.mp4"
    source.write_bytes(b"bad")
    media = Media(MediaConfig())
    monkeypatch.setattr(media, "run", lambda *args, **kwargs: raw)
    with pytest.raises(MediaError, match="INVALID_MEDIA_METADATA"):
        media.probe(source)


@pytest.mark.media
def test_real_extract_normalize_and_remove_all_audio(source_video, real_media):
    from video_translate.state import fingerprint
    before = fingerprint(source_video)
    assert len(real_media.probe(source_video)["streams"]) == 3
    audio = real_media.extract_audio(source_video, source_video.parent / "识别 音频.wav")
    wav = real_media.normalize_audio(audio, source_video.parent / "统一 音频.wav")
    mute = real_media.video_without_audio(source_video, source_video.parent / "全部 静音.mp4")
    assert real_media.probe(audio)["streams"][0]["sample_rate"] == "16000"
    assert real_media.probe(wav)["streams"][0]["sample_rate"] == "48000"
    assert real_media.probe(wav)["streams"][0]["channels"] == 2
    assert [stream["codec_type"] for stream in real_media.probe(mute)["streams"]] == ["video"]
    assert fingerprint(source_video) == before
    assert not list(source_video.parent.glob(".*.tmp*"))


@pytest.mark.media
def test_no_audio_corrupt_input_and_overwrite(source_video, real_media):
    with pytest.raises(MediaError, match="SOURCE_OVERWRITE_FORBIDDEN"):
        real_media.video_without_audio(source_video, source_video)
    mute = real_media.video_without_audio(source_video, source_video.parent / "mute.mp4")
    with pytest.raises(MediaError, match="OUTPUT_ALREADY_EXISTS"):
        real_media.video_without_audio(source_video, mute)
    output = source_video.parent / "missing.wav"
    with pytest.raises(MediaError):
        real_media.extract_audio(mute, output)
    assert not output.exists()
    corrupt = source_video.parent / "损坏.mp4"
    corrupt.write_bytes(b"invalid")
    with pytest.raises(MediaError):
        real_media.probe(corrupt)
    assert not list(source_video.parent.glob(".*.tmp*"))


def test_cleanup_can_disable_subprocess_deadline_without_changing_other_calls(monkeypatch, tmp_path):
    captured = []
    monkeypatch.setattr("video_translate.media.executable", lambda name: name)
    def run(args, **kwargs):
        captured.append(kwargs["timeout"])
        return subprocess.CompletedProcess(args, 0, stdout="")
    monkeypatch.setattr("video_translate.media.subprocess.run", run)
    media = Media(MediaConfig(timeout_seconds=300))
    media.run("ffmpeg", [], cwd=tmp_path, timeout_seconds=None)
    media.run("ffprobe", [], cwd=tmp_path)
    assert captured == [None, 300]
