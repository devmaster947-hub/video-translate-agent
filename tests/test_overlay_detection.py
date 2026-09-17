from video_translate.overlay_detection import build_text_tracks, group_watermarks, sample_timestamps
from video_translate.policy_client import merge_cleanup_policy


def observation(timestamp, text, box):
    return {"timestamp_ms": timestamp, "text": text, "bbox": box, "bbox_norm": box,
            "confidence": .95, "polygon": [], "polygon_norm": []}


def test_sampling_is_uniform_and_capped():
    values = sample_timestamps(60_000, 4, 240)
    assert len(values) == 240 and values[0] == 0 and values[-1] == 59_999
    assert len(sample_timestamps(10_000, 4, 240)) == 40


def test_track_building_remains_local():
    box = {"x": .2, "y": .7, "w": .6, "h": .08}
    items = [observation(0, "hello", box), observation(250, "hello", box)]
    tracks = build_text_tracks(items, 250)
    assert len(tracks) == 1 and len(tracks[0]["observations"]) == 2


def test_remote_cleanup_decisions_merge_without_local_policy():
    box = {"x": .2, "y": .7, "w": .6, "h": .08}
    tracks = [{"track_id": "text_0001", "text": "hello", "bbox_norm": box,
               "start_ms": 0, "end_ms": 500, "observations": [observation(0, "hello", box)]}]
    layout = {"version": 2, "strategy": "remote", "default": {"center_x": .5, "bottom_y": .8,
              "width": .7, "height": .08}, "segments": {}}
    result = merge_cleanup_policy(tracks, {"success": True, "tracks": [{
        "track_id": "text_0001", "kind": "subtitle", "speech_overlap": .9,
        "persistence": .5, "layout_eligible": True, "cleanup_eligible": True,
    }], "subtitle_layout": layout})
    assert result == layout
    assert tracks[0]["kind"] == "subtitle" and tracks[0]["cleanup_eligible"] is True


def test_same_watermark_in_different_corners_is_grouped_after_remote_classification():
    first = {"track_id":"text_0001","text":"@creator","kind":"watermark"}
    second = {"track_id":"text_0002","text":"@creator","kind":"watermark"}
    groups = group_watermarks([first, second])
    assert len(groups) == 1 and groups[0]["track_ids"] == ["text_0001", "text_0002"]
