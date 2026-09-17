from pathlib import Path
from types import SimpleNamespace as NS
import shutil

import cv2
import numpy as np

from video_translate.cleanup import clean_video
from video_translate.config import CleanupConfig, Config
from video_translate.files import write_json
from video_translate.models import Phase, Segment
from video_translate.state import JobStore, fingerprint
from video_translate.workflow import align_job, clean_job


class FakeOCR:
    def __init__(self):
        self.calls = 0

    def __call__(self, frame, **kwargs):
        self.calls += 1
        text = "FIRST MESSAGE" if self.calls == 1 else "SECOND OFFER"
        return NS(boxes=np.array([[[45, 80], [275, 80], [275, 110], [45, 110]]]),
                  txts=(text,), scores=(.99,))


class FakeMedia:
    config = NS(ffmpeg="ffmpeg")

    def require_video(self, path):
        capture = cv2.VideoCapture(str(path))
        fps = capture.get(cv2.CAP_PROP_FPS)
        frames = capture.get(cv2.CAP_PROP_FRAME_COUNT)
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        capture.release()
        return {"duration_ms": round(frames / fps * 1000),
                "streams": [{"codec_type": "video", "width": width, "height": height}]}

    def run(self, tool, arguments, cwd=None, timeout_seconds=None):
        if "null" in arguments:
            return ""
        source = Path(arguments[arguments.index("-i") + 1])
        output = Path(arguments[-1])
        shutil.copyfile(source, output)
        return ""


def make_caption_video(path):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 25, (320, 180))
    for _ in range(20):
        frame = np.full((180, 320, 3), 90, np.uint8)
        cv2.putText(frame, "ORIGINAL SUBTITLE", (48, 105), cv2.FONT_HERSHEY_SIMPLEX,
                    .55, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(frame, "ORIGINAL SUBTITLE", (48, 105), cv2.FONT_HERSHEY_SIMPLEX,
                    .55, (255, 255, 255), 1, cv2.LINE_AA)
        writer.write(frame)
    writer.release()


def frame_at(path, index=10):
    capture = cv2.VideoCapture(str(path))
    capture.set(cv2.CAP_PROP_POS_FRAMES, index)
    ok, frame = capture.read()
    capture.release()
    assert ok
    return frame


def test_cleanup_changes_glyph_region_preserves_source_and_duration(tmp_path):
    source = tmp_path / "source.avi"
    make_caption_video(source)
    before_hash = fingerprint(source)
    def policy(payload):
        tracks = [{"track_id": t["track_id"], "kind": "subtitle", "speech_overlap": 1.0,
                   "persistence": .5, "layout_eligible": True, "cleanup_eligible": True}
                  for t in payload["tracks"]]
        return {"success": True, "tracks": tracks,
                "subtitle_layout": {"version": 2, "strategy": "remote",
                    "source_track_ids": [t["track_id"] for t in payload["tracks"]],
                    "default": {"center_x": .5, "bottom_y": .62, "width": .72, "height": .1},
                    "segments": {}}}
    result = clean_video(source, tmp_path, [NS(id=1, start_ms=0, end_ms=800)],
                         CleanupConfig(backend="opencv", ), FakeMedia(), ocr_engine=FakeOCR(), policy_requester=policy)
    before, after = frame_at(source), frame_at(result["video"])
    assert np.mean(cv2.absdiff(before[70:125, 40:280], after[70:125, 40:280])) > 0.5
    assert fingerprint(source) == before_hash
    assert abs(FakeMedia().require_video(source)["duration_ms"]
               - FakeMedia().require_video(result["video"])["duration_ms"]) <= 100
    assert (tmp_path / "cleanup_report.json").exists()
    metric = result["report"]["mask_metrics"][0]
    assert metric["kind"] == "subtitle"
    assert metric["mask_pixels"] > 0
    assert 0.05 <= metric["bbox_coverage"] < 1.0
    assert metric["dynamic_frames"] > 0
    assert metric["mean_dynamic_pixels"] > 0
    assert sum(result["report"]["repair_methods"].values()) > 0
    assert all((tmp_path / "preview" / name).exists() for name in
               ("cleanup_before.png", "cleanup_mask.png", "cleanup_after.png"))


def test_cleaned_phase_resumes_without_reprocessing(tmp_path, monkeypatch):
    config = Config(runtime_root=tmp_path)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fixture")
    store = JobStore(tmp_path)
    manifest = store.create(source, config.snapshot())
    store.transition(manifest.job_id, Phase.LANG_CONFIRMED, source_language="en", target_language="zh-CN")
    store.transition(manifest.job_id, Phase.TRANSCRIBED)
    directory = store.directory(manifest.job_id)
    write_json(directory / "segments_raw.json", [{"id": 1, "start_ms": 0, "end_ms": 500,
                                                   "raw_text": "x", "text": "x"}])
    calls = []
    def fake_clean(*args, **kwargs):
        calls.append(1)
        (directory / "clean").mkdir()
        (directory / "clean/video.mp4").write_bytes(b"clean")
        for name, value in (("overlay_analysis.json", {}), ("subtitle_layout.json", {}),
                            ("cleanup_report.json", {})):
            write_json(directory / name, value)
        return {}
    monkeypatch.setattr("video_translate.cleanup.clean_video", fake_clean)
    first = clean_job(store, manifest.job_id, config, NS())
    second = clean_job(JobStore(tmp_path), manifest.job_id, config, NS())
    assert first.phase == Phase.CLEANED and second == first and calls == [1]


def test_alignment_uses_committed_clean_video(tmp_path, monkeypatch):
    config = Config(runtime_root=tmp_path)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    store = JobStore(tmp_path)
    manifest = store.create(source, config.snapshot())
    job_id, directory = manifest.job_id, store.directory(manifest.job_id)
    store.transition(job_id, Phase.LANG_CONFIRMED, source_language="en", target_language="zh-CN")
    store.transition(job_id, Phase.TRANSCRIBED)
    clean_path = directory / "clean/video.mp4"
    clean_path.parent.mkdir()
    clean_path.write_bytes(b"clean")
    store.record_artifact(job_id, "cleaned_video", "clean/video.mp4")
    for phase in (Phase.CLEANED, Phase.POLISHED, Phase.TRANSLATED, Phase.WAITING_VOICE, Phase.DUBBED):
        store.transition(job_id, phase)
    segment = Segment(id=1, start_ms=0, end_ms=500, raw_text="x", polished_text="x",
                      translated_text="y", tts_file="tts/0001.wav", tts_duration_ms=500)
    write_json(directory / "segments_dubbed.json", [segment.model_dump(mode="json")])
    seen = []
    def fake_align(segments, source_video, job_directory, media, settings, **kwargs):
        seen.append(source_video)
        for name in ("alignment_plan.json", "alignment_result.json"):
            write_json(job_directory / name, {})
        (job_directory / "aligned").mkdir()
        (job_directory / "aligned/video.mp4").write_bytes(b"aligned")
        (job_directory / "target.wav").write_bytes(b"audio")
        return [Segment.model_validate(segments[0].model_dump() | {"final_start_ms": 0, "final_end_ms": 500})]
    monkeypatch.setattr("video_translate.alignment.align_segments", fake_align)
    align_job(store, job_id, config, NS())
    assert seen == [clean_path]




def _empty_analysis(source, segments, config, **kwargs):
    return {"video": {"width": 320, "height": 180, "duration_ms": 800},
            "tracks": [], "ocr_seconds": 0, "sample_timestamps_ms": []}, {}


def test_slow_cleanup_ignores_legacy_deadline_and_keeps_json(monkeypatch, tmp_path, capsys):
    import json
    import video_translate.cleanup as cleanup
    from video_translate.workflow import job_config
    source = tmp_path / "source.avi"
    make_caption_video(source)
    config = Config(runtime_root=tmp_path, cleanup={"backend": "opencv", "max_runtime_seconds": 180})
    recovered = job_config(NS(config_snapshot=config.snapshot()), config)
    assert "max_runtime_seconds" not in config.snapshot().model_dump()["cleanup"]
    clock = [0.0]
    def tick():
        clock[0] += 11.0
        return clock[0]
    def slow_analysis(*args, **kwargs):
        clock[0] += 200.0
        return _empty_analysis(*args, **kwargs)
    monkeypatch.setattr(cleanup.time, "perf_counter", tick)
    monkeypatch.setattr(cleanup, "analyze_video", slow_analysis)
    seen = []
    class SlowMedia(FakeMedia):
        def run(self, tool, arguments, cwd=None, **kwargs):
            seen.append(kwargs)
            clock[0] += 400.0
            return super().run(tool, arguments, cwd, **kwargs)
    result = clean_video(source, tmp_path, [], recovered.cleanup, SlowMedia())
    print(json.dumps({"ok": True}))
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"ok": True}
    assert "frames" in captured.err and "elapsed" in captured.err and "100.0%" in captured.err
    assert result["report"]["total_seconds"] > 180
    assert result["report"]["encode_seconds"] > 300
    assert result["report"]["processed_frames"] == 20
    assert all(x["timeout_seconds"] is None for x in seen)
    assert result["video"].exists()
    assert not list((tmp_path / "clean").glob(".*.avi"))
    assert not list((tmp_path / "clean").glob(".*.mp4"))


def test_cleanup_cancel_releases_handles_removes_temp_and_can_retry(monkeypatch, tmp_path):
    import pytest
    import video_translate.cleanup as cleanup
    source = tmp_path / "source.avi"
    make_caption_video(source)
    monkeypatch.setattr(cleanup, "analyze_video", _empty_analysis)
    real_writer = cv2.VideoWriter
    released = []
    class CancelWriter:
        def __init__(self, *args):
            self.inner = real_writer(*args)
        def isOpened(self): return self.inner.isOpened()
        def write(self, frame): raise KeyboardInterrupt()
        def release(self):
            self.inner.release()
            released.append(True)
    monkeypatch.setattr(cv2, "VideoWriter", CancelWriter)
    with pytest.raises(KeyboardInterrupt):
        clean_video(source, tmp_path, [], CleanupConfig(backend="opencv"), FakeMedia())
    assert released
    assert not list((tmp_path / "clean").iterdir())
    monkeypatch.setattr(cv2, "VideoWriter", real_writer)
    result = clean_video(source, tmp_path, [], CleanupConfig(backend="opencv"), FakeMedia())
    assert result["report"]["processed_frames"] == 20


def test_encoding_failure_does_not_publish_and_preserves_cache(monkeypatch, tmp_path):
    import pytest
    import video_translate.cleanup as cleanup
    from video_translate.media import MediaError
    source = tmp_path / "source.avi"
    make_caption_video(source)
    cache = tmp_path / "cleanup_policy_result.json"
    cache.write_text('{"fixture": "preserve"}')
    monkeypatch.setattr(cleanup, "analyze_video", _empty_analysis)
    class BrokenMedia(FakeMedia):
        def run(self, tool, arguments, **kwargs):
            Path(arguments[-1]).write_bytes(b"partial")
            raise MediaError("MEDIA_EXECUTION_FAILED")
    with pytest.raises(MediaError):
        clean_video(source, tmp_path, [], CleanupConfig(backend="opencv"), BrokenMedia())
    assert not list((tmp_path / "clean").iterdir())
    assert cache.read_text() == '{"fixture": "preserve"}'


def test_real_cleanup_encoding_without_deadline(monkeypatch, tmp_path, real_media):
    import video_translate.cleanup as cleanup
    source = tmp_path / "source.avi"
    make_caption_video(source)
    monkeypatch.setattr(cleanup, "analyze_video", _empty_analysis)
    result = clean_video(source, tmp_path, [], CleanupConfig(backend="opencv"), real_media)
    assert result["report"]["processed_frames"] == 20
    assert real_media.require_video(result["video"])["duration_ms"] == 800


def test_legacy_failed_job_resumes_same_id_without_manifest_edits(monkeypatch, tmp_path):
    import json
    import video_translate.cleanup as cleanup
    source = tmp_path / "source.avi"
    make_caption_video(source)
    config = Config(runtime_root=tmp_path, cleanup={"backend": "opencv"})
    store = JobStore(tmp_path)
    manifest = store.create(source, config.snapshot())
    job_id = manifest.job_id
    store.transition(job_id, Phase.LANG_CONFIRMED, source_language="en", target_language="zh-CN")
    store.transition(job_id, Phase.TRANSCRIBED)
    directory = store.directory(job_id)
    write_json(directory / "segments_raw.json", [])
    store.fail(job_id, Phase.CLEANED, "CLEANUP_TIMEOUT")
    path = directory / "manifest.json"
    data = json.loads(path.read_text())
    data["config_snapshot"]["cleanup"]["max_runtime_seconds"] = 180
    write_json(path, data)  # Simulates the old manifest written by 2.3.0.
    cache = directory / "cleanup_policy_result.json"
    cache.write_text('{"fixture":"preserve"}')
    monkeypatch.setattr(cleanup, "analyze_video", _empty_analysis)
    result = clean_job(JobStore(tmp_path), job_id, config, FakeMedia())
    assert result.job_id == job_id and result.phase == Phase.CLEANED
    assert result.error is None
    assert cache.read_text() == '{"fixture":"preserve"}'
    assert store.verify(result) == []
