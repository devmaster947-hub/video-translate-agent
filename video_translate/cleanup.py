"""Fast overlay cleanup using sparse OCR masks and ROI-only OpenCV Telea inpainting."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from uuid import uuid4

from .media import MediaError
from .files import write_json
from .overlay_detection import analyze_video
from .overlay_mask import dynamic_track_mask, manual_region_mask, track_mask


class CleanupError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _roi(mask):
    import cv2
    points = cv2.findNonZero(mask)
    return cv2.boundingRect(points) if points is not None else None


def _mask_metric(track, mask, width, height):
    import cv2

    bbox = track.get("bbox_norm") or {}
    x1 = max(0, min(width, round(float(bbox.get("x", 0)) * width)))
    y1 = max(0, min(height, round(float(bbox.get("y", 0)) * height)))
    x2 = max(x1, min(width, round(float(bbox.get("x", 0) + bbox.get("w", 0)) * width)))
    y2 = max(y1, min(height, round(float(bbox.get("y", 0) + bbox.get("h", 0)) * height)))
    bbox_area = max(1, (x2 - x1) * (y2 - y1))
    pixels = int(cv2.countNonZero(mask))
    return {"track_id": track["track_id"], "kind": track["kind"],
            "mask_pixels": pixels, "bbox_pixels": bbox_area,
            "bbox_coverage": round(pixels / bbox_area, 4)}


def _adaptive_inpaint(frame, mask, bounds, radius):
    """Pick a fast OpenCV repair mode from local mask density and texture."""
    import cv2
    import numpy as np

    x, y, w, h = bounds
    roi = frame[y:y+h, x:x+w]
    roi_mask = mask[y:y+h, x:x+w]
    density = cv2.countNonZero(roi_mask) / max(1, roi_mask.size)
    outside = cv2.bitwise_not(roi_mask) > 0
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    texture = float(np.std(gray[outside])) if np.any(outside) else 0.0
    # Telea removes compact glyphs more reliably.  Navier-Stokes is reserved for
    # broad, low-texture manual/solid masks; on high-contrast subtitle strokes it
    # can propagate the white glyph edge back into the hole.
    method = cv2.INPAINT_NS if density >= 0.78 and texture < 20 else cv2.INPAINT_TELEA
    return cv2.inpaint(roi, roi_mask, radius, method), ("ns" if method == cv2.INPAINT_NS else "telea")


def _public_analysis(analysis):
    result = dict(analysis)
    result["tracks"] = []
    for track in analysis["tracks"]:
        item = {key: value for key, value in track.items() if key != "observations"}
        item["observations"] = [{key: value for key, value in observation.items()
                                 if key != "polygon"} for observation in track["observations"]]
        result["tracks"].append(item)
    return result


def _clean_video_local(source: Path, directory: Path, segments, config, media, ocr_engine=None, *, job_id=None, policy_requester=None, prepared=None):
    import cv2
    import numpy as np

    started = time.perf_counter()
    print("OpenCV cleanup: analyzing text tracks", file=sys.stderr, flush=True)
    clean_dir, masks_dir, preview_dir = directory / "clean", directory / "masks", directory / "preview"
    clean_dir.mkdir(exist_ok=True)
    masks_dir.mkdir(exist_ok=True)
    preview_dir.mkdir(exist_ok=True)
    output = clean_dir / "video.mp4"
    if output.exists():
        output.unlink()

    if config.mode != "local":
        raise CleanupError("UNSUPPORTED_CLEANUP_MODE")
    if not config.enabled:
        media.video_without_audio(source, output)
        info = media.require_video(output)
        analysis = {"version": 1, "video": {"width": 0, "height": 0,
                    "duration_ms": info["duration_ms"]}, "sample_timestamps_ms": [],
                    "ocr_seconds": 0.0, "tracks": [], "disabled": True}
        layout = {"version": 1, "default": None, "segments": {}}
        report = {"ocr_seconds": 0.0, "mask_seconds": 0.0, "inpaint_seconds": 0.0,
                  "encode_seconds": 0.0, "total_seconds": round(time.perf_counter() - started, 3),
                  "sample_count": 0, "processed_frames": 0, "removable_track_count": 0,
                  "mask_metrics": [],
                  "repair_methods": {"telea": 0, "ns": 0},
                  "benchmark_roi_frames": 0, "benchmark_seconds": 0.0, "disabled": True}
        write_json(directory / "overlay_analysis.json", analysis)
        write_json(directory / "subtitle_layout.json", layout)
        write_json(directory / "cleanup_report.json", report)
        return {"video": output, "analysis": analysis, "layout": layout, "report": report}

    analysis, layout = prepared if prepared is not None else analyze_video(
        source, segments, config, engine=ocr_engine, job_id=job_id, policy_requester=policy_requester,
        policy_cache=directory / "cleanup_policy_result.json")

    width, height = analysis["video"]["width"], analysis["video"]["height"]
    removable = [track for track in analysis["tracks"]
                 if track.get("cleanup_eligible") is True]
    mask_started = time.perf_counter()
    masks = []
    for track in removable:
        mask = track_mask(source, track, config)
        relative = f"masks/{track['track_id']}.png"
        if not cv2.imwrite(str(directory / relative), mask):
            raise CleanupError("MASK_WRITE_FAILED")
        track["mask"] = relative
        masks.append({"track": track, "anchor": mask, "bounds": _roi(mask),
                      "dynamic_pixels": 0, "dynamic_frames": 0})

    for index, region in enumerate(config.manual_watermark_regions, 1):
        mask = manual_region_mask(width, height, region)
        track = {"track_id": f"watermark_manual_{index:04d}", "kind": "watermark",
                 "start_ms": 0, "end_ms": analysis["video"]["duration_ms"],
                 "bbox_norm": region.model_dump(), "manual": True,
                 "mask": f"masks/watermark_manual_{index:04d}.png", "observations": []}
        cv2.imwrite(str(directory / track["mask"]), mask)
        analysis["tracks"].append(track)
        masks.append({"track": track, "anchor": mask, "bounds": _roi(mask),
                      "dynamic_pixels": 0, "dynamic_frames": 0})
    mask_seconds = time.perf_counter() - mask_started

    capture = cv2.VideoCapture(str(source))
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    expected_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    if not capture.isOpened() or fps <= 0:
        capture.release()
        raise CleanupError("CLEANUP_VIDEO_OPEN_FAILED")
    temporary_frames = clean_dir / f".frames-{uuid4().hex}.avi"
    encoded = clean_dir / f".encoded-{uuid4().hex}.mp4"
    writer = None
    try:
        writer = cv2.VideoWriter(str(temporary_frames), cv2.VideoWriter_fourcc(*"MJPG"), fps, (width, height))
        if not writer.isOpened():
            raise CleanupError("CLEANUP_ENCODER_UNAVAILABLE")
        inpaint_started = time.perf_counter()
        last_progress = inpaint_started
        print(f"OpenCV cleanup: 0/{expected_frames} frames (0.0%), elapsed {inpaint_started-started:.1f}s",
              file=sys.stderr, flush=True)
        frame_index = 0
        preview = None
        fallback_preview = None
        benchmark_elapsed = 0.0
        benchmark_count = 0
        repair_methods = {"telea": 0, "ns": 0}
        try:
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                timestamp_ms = round(frame_index / fps * 1000)
                combined = np.zeros((height, width), dtype=np.uint8)
                active = []
                for item in masks:
                    track, anchor, bounds = item["track"], item["anchor"], item["bounds"]
                    if bounds and track["start_ms"] <= timestamp_ms <= track["end_ms"]:
                        mask = (anchor if track.get("manual") else
                                dynamic_track_mask(frame, track, anchor, timestamp_ms, config))
                        dynamic_bounds = _roi(mask)
                        if dynamic_bounds:
                            combined = cv2.bitwise_or(combined, mask)
                            active.append((track, mask, dynamic_bounds))
                            item["dynamic_pixels"] += cv2.countNonZero(mask)
                            item["dynamic_frames"] += 1
                cleaned = frame.copy()
                for _track, mask, bounds in active:
                    x, y, w, h = bounds
                    radius = max(1.0, config.inpaint_radius_px_720p * height / 720)
                    tick = time.perf_counter()
                    repaired, method = _adaptive_inpaint(cleaned, mask, bounds, radius)
                    cleaned[y:y+h, x:x+w] = repaired
                    repair_methods[method] += 1
                    if benchmark_count < 30:
                        benchmark_elapsed += time.perf_counter() - tick
                        benchmark_count += 1
                if fallback_preview is None:
                    fallback_preview = (frame.copy(), combined.copy(), cleaned.copy())
                if preview is None and np.any(combined):
                    preview = (frame.copy(), combined.copy(), cleaned.copy())
                writer.write(cleaned)
                frame_index += 1
                now = time.perf_counter()
                if now - last_progress >= 10:
                    percentage = (f"{min(100.0, frame_index / expected_frames * 100):.1f}%"
                                  if expected_frames > 0 else "unknown")
                    print(f"OpenCV cleanup: {frame_index}/{expected_frames} frames ({percentage}), "
                          f"elapsed {now-started:.1f}s", file=sys.stderr, flush=True)
                    last_progress = now

        finally:
            capture.release()
            writer.release()
        inpaint_seconds = time.perf_counter() - inpaint_started
        if frame_index == 0:
            temporary_frames.unlink(missing_ok=True)
            raise CleanupError("CLEANUP_NO_FRAMES")
        if expected_frames > 0 and frame_index != expected_frames:
            raise CleanupError("CLEANUP_FRAME_COUNT_MISMATCH")
        print(f"OpenCV cleanup: {frame_index}/{expected_frames} frames (100.0%), "
              f"elapsed {time.perf_counter()-started:.1f}s; encoding", file=sys.stderr, flush=True)
        preview = preview or fallback_preview
        if preview is not None:
            for name, image in zip(("cleanup_before.png", "cleanup_mask.png", "cleanup_after.png"), preview):
                if not cv2.imwrite(str(preview_dir / name), image):
                    raise CleanupError("CLEANUP_PREVIEW_WRITE_FAILED")

        encode_started = time.perf_counter()
        media.run(media.config.ffmpeg, ["-hide_banner", "-nostdin", "-v", "error", "-y",
                  "-i", str(temporary_frames), "-map", "0:v:0", "-an", "-sn", "-dn",
                  "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
                  "-movflags", "+faststart", str(encoded)], cwd=clean_dir, timeout_seconds=None)
        source_duration = media.require_video(source)["duration_ms"]
        clean_info = media.require_video(encoded)
        if abs(clean_info["duration_ms"] - source_duration) > 100:
            raise CleanupError("CLEANUP_DURATION_MISMATCH")
        media.run(media.config.ffmpeg, ["-v", "error", "-nostdin", "-xerror", "-i", str(encoded),
                  "-f", "null", "-"], cwd=clean_dir, timeout_seconds=None)
        encode_seconds = time.perf_counter() - encode_started


        total = time.perf_counter() - started
        removable_count = sum(track.get("cleanup_eligible") is True
                              for track in analysis["tracks"])
        mask_metrics = []
        for item in masks:
            metric = _mask_metric(item["track"], item["anchor"], width, height)
            metric["mean_dynamic_pixels"] = round(
                item["dynamic_pixels"] / max(1, item["dynamic_frames"]), 1)
            metric["dynamic_frames"] = item["dynamic_frames"]
            mask_metrics.append(metric)
        report = {"ocr_seconds": analysis["ocr_seconds"], "mask_seconds": round(mask_seconds, 3),
                  "inpaint_seconds": round(inpaint_seconds, 3), "encode_seconds": round(encode_seconds, 3),
                  "total_seconds": round(total, 3), "sample_count": len(analysis["sample_timestamps_ms"]),
                  "processed_frames": frame_index, "removable_track_count": removable_count,
                  "mask_metrics": mask_metrics,
                  "repair_methods": repair_methods,
                  "benchmark_roi_frames": benchmark_count,
                  "benchmark_seconds": round(benchmark_elapsed, 3)}
        write_json(directory / "overlay_analysis.json", _public_analysis(analysis))
        write_json(directory / "subtitle_layout.json", layout)
        write_json(directory / "cleanup_report.json", report)
        os.link(encoded, output)
        return {"video": output, "analysis": analysis, "layout": layout, "report": report}
    finally:
        capture.release()
        if writer is not None:
            writer.release()
        temporary_frames.unlink(missing_ok=True)
        encoded.unlink(missing_ok=True)


def clean_video(source: Path, directory: Path, segments, config, media, ocr_engine=None,
                *, job_id=None, policy_requester=None):
    """Local GPU cleanup with OpenCV fallback."""
    if not config.enabled:
        return _clean_video_local(source, directory, segments, config, media,
            ocr_engine=ocr_engine, job_id=job_id, policy_requester=policy_requester)
    from .sttn import select_backend, clean_sttn, cached_analysis, STTNError
    import copy
    backend, device, reason = select_backend(config.backend)
    prepared = None
    if backend == "sttn":
        analysis, layout = cached_analysis(source, directory, segments, config, analyze_video, engine=ocr_engine,
            job_id=job_id, policy_requester=policy_requester,
    policy_cache=directory / "cleanup_policy_result.json")
        prepared = (analysis, layout)
        try:
            result = clean_sttn(source, directory, copy.deepcopy(analysis), layout, config, media, device)
            write_json(directory / "overlay_analysis.json", _public_analysis(result["analysis"]))
            write_json(directory / "subtitle_layout.json", layout)
            write_json(directory / "cleanup_report.json", result["report"])
            return result
        except Exception as exc:
            reason = str(exc) if isinstance(exc, (STTNError, MediaError)) else "STTN_EXECUTION_FAILED"
            (directory / "clean/video.mp4").unlink(missing_ok=True)
    if reason:
        import sys
        print(f"STTN fallback to OpenCV: {reason}", file=sys.stderr, flush=True)
    result = _clean_video_local(source, directory, segments, config, media,
        ocr_engine=ocr_engine, job_id=job_id, policy_requester=policy_requester, prepared=prepared)
    result["report"].update(backend="opencv", device="cpu", fallback_reason=reason)
    write_json(directory / "cleanup_report.json", result["report"])
    return result
