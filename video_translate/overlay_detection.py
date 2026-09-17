"""Sparse OCR, conservative text tracking, and subtitle layout extraction."""
from __future__ import annotations

import re
import time
from difflib import SequenceMatcher
from math import hypot


def sample_timestamps(duration_ms: int, sample_fps: float, max_frames: int) -> list[int]:
    if duration_ms <= 0 or sample_fps <= 0 or max_frames < 1:
        raise ValueError("Invalid OCR sampling parameters")
    count = min(max(1, int(duration_ms / 1000 * sample_fps)), max_frames)
    if count == 1:
        return [duration_ms // 2]
    return [round(i * max(0, duration_ms - 1) / (count - 1)) for i in range(count)]


def _text_key(text: str) -> str:
    return re.sub(r"[^\w@.]+", "", text.casefold(), flags=re.UNICODE)


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _text_key(a), _text_key(b)).ratio()


def _bbox(polygon, width: int, height: int) -> tuple[dict, dict]:
    xs = [max(0.0, min(float(point[0]), width - 1)) for point in polygon]
    ys = [max(0.0, min(float(point[1]), height - 1)) for point in polygon]
    x1, x2, y1, y2 = min(xs), max(xs), min(ys), max(ys)
    pixels = {"x": x1, "y": y1, "w": max(1.0, x2 - x1), "h": max(1.0, y2 - y1)}
    normalized = {"x": pixels["x"] / width, "y": pixels["y"] / height,
                  "w": pixels["w"] / width, "h": pixels["h"] / height}
    return pixels, normalized


def _center(box):
    return box["x"] + box["w"] / 2, box["y"] + box["h"] / 2


def _iou(a, b):
    left, top = max(a["x"], b["x"]), max(a["y"], b["y"])
    right = min(a["x"] + a["w"], b["x"] + b["w"])
    bottom = min(a["y"] + a["h"], b["y"] + b["h"])
    intersection = max(0, right - left) * max(0, bottom - top)
    union = a["w"] * a["h"] + b["w"] * b["h"] - intersection
    return intersection / union if union else 0.0


def parse_rapidocr(result, timestamp_ms: int, width: int, height: int,
                   min_confidence: float) -> list[dict]:
    """Normalize RapidOCR >=3.9 output without accepting the legacy package shape only."""
    boxes = getattr(result, "boxes", None)
    texts = getattr(result, "txts", None)
    scores = getattr(result, "scores", None)
    if boxes is None and isinstance(result, (tuple, list)) and len(result) >= 3:
        boxes, texts, scores = result[:3]
    if boxes is None or texts is None or scores is None:
        return []
    items = []
    for polygon, text, score in zip(boxes, texts, scores):
        text = str(text).strip()
        score = float(score)
        points = [[float(point[0]), float(point[1])] for point in polygon]
        if text and score >= min_confidence and len(points) >= 4:
            box, box_norm = _bbox(points, width, height)
            items.append({"timestamp_ms": timestamp_ms, "text": text, "confidence": score,
                          "polygon": points, "polygon_norm": [[x / width, y / height] for x, y in points],
                          "bbox": box, "bbox_norm": box_norm})
    return items


def build_text_tracks(observations: list[dict], sample_interval_ms: float = 250) -> list[dict]:
    tracks: list[dict] = []
    max_gap = max(1200, round(sample_interval_ms * 3.5))
    for item in sorted(observations, key=lambda value: value["timestamp_ms"]):
        best, best_score = None, -1.0
        for track in tracks:
            previous = track["observations"][-1]
            gap = item["timestamp_ms"] - previous["timestamp_ms"]
            if gap < 0 or gap > max_gap:
                continue
            item_box = item.get("bbox_norm", item["bbox"])
            previous_box = previous.get("bbox_norm", previous["bbox"])
            overlap = _iou(item_box, previous_box)
            distance = hypot(*[a - b for a, b in zip(_center(item_box), _center(previous_box))])
            text_score = _similarity(item["text"], previous["text"])
            score = overlap * 2 + text_score - distance
            if (overlap >= 0.18 or distance <= 0.055) and text_score >= 0.76 and score > best_score:
                best, best_score = track, score
        if best is None:
            best = {"track_id": f"text_{len(tracks) + 1:04d}", "observations": []}
            tracks.append(best)
        best["observations"].append(item)
    for track in tracks:
        seen = track["observations"]
        track.update(start_ms=seen[0]["timestamp_ms"], end_ms=seen[-1]["timestamp_ms"],
                     text=seen[len(seen) // 2]["text"],
                     bbox_norm=_median_box([o.get("bbox_norm", o["bbox"]) for o in seen]))
    return tracks


def _median(values):
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def _median_box(boxes):
    return {name: _median([box[name] for box in boxes]) for name in ("x", "y", "w", "h")}


def group_watermarks(tracks: list[dict]) -> list[dict]:
    groups = []
    for track in (item for item in tracks if item["kind"] == "watermark"):
        group = next((item for item in groups if _similarity(item["text"], track["text"]) >= .82), None)
        if group is None:
            group = {"id": f"watermark_group_{len(groups) + 1:04d}", "text": track["text"],
                     "track_ids": []}
            groups.append(group)
        group["track_ids"].append(track["track_id"])
        track["watermark_group"] = group["id"]
    return groups


def _layout(box):
    return {"center_x": round(box["x"] + box["w"] / 2, 6),
            "bottom_y": round(box["y"] + box["h"], 6),
            "width": round(box["w"], 6), "height": round(box["h"], 6)}


def analyze_video(video, segments, config, engine=None, *, job_id=None, policy_requester=None, policy_cache=None) -> tuple[dict, dict]:
    import cv2

    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise ValueError("Unable to open video for OCR")
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    duration_ms = round(frames / fps * 1000) if fps > 0 else 0
    timestamps = sample_timestamps(duration_ms, config.sample_fps, config.max_ocr_frames)
    if engine is None:
        from rapidocr import RapidOCR
        engine = RapidOCR()
    ocr = engine
    started = time.perf_counter()
    observations = []
    try:
        for timestamp_ms in timestamps:
            capture.set(cv2.CAP_PROP_POS_MSEC, timestamp_ms)
            ok, frame = capture.read()
            if not ok:
                continue
            observations.extend(parse_rapidocr(
                ocr(frame, text_score=config.min_ocr_confidence), timestamp_ms,
                width, height, config.min_ocr_confidence))
    finally:
        capture.release()
    interval = duration_ms / max(1, len(timestamps) - 1)
    tracks = build_text_tracks(observations, interval)
    for index, track in enumerate(tracks, 1):
        track["start_ms"] = max(0, round(track["start_ms"] - interval / 2))
        track["end_ms"] = min(duration_ms, round(track["end_ms"] + interval))
        # Stable local IDs identify tracks in remote decisions and local masks.
        track["track_id"] = f"text_{index:04d}"
    from .policy_client import resolve_cleanup_policy
    kwargs = {} if policy_requester is None else {"requester": policy_requester}
    layout = resolve_cleanup_policy(job_id=job_id,
        video={"width": width, "height": height, "duration_ms": duration_ms},
        sample_count=len(timestamps), tracks=tracks, segments=segments, config=config, cache_path=policy_cache, **kwargs)
    watermark_groups = group_watermarks(tracks)
    analysis = {"version": 1, "video": {"width": width, "height": height,
                "duration_ms": duration_ms}, "sample_timestamps_ms": timestamps,
                "ocr_seconds": round(time.perf_counter() - started, 3), "tracks": tracks,
                "cleanup_policy_backend": "remote",
                "watermark_groups": watermark_groups}
    return analysis, layout
