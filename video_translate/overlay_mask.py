"""Static OCR anchors and per-frame glyph masks for overlay cleanup."""
from __future__ import annotations


def scaled_px(value, height):
    return max(0, round(value * height / 720))


def glyph_candidate(frame, polygon, config):
    import cv2
    import numpy as np

    height, width = frame.shape[:2]
    points = np.asarray(polygon, dtype=np.int32)
    x, y, w, h = cv2.boundingRect(points)
    padding = scaled_px(config.mask_roi_padding_px_720p, height)
    x1, y1 = max(0, x - padding), max(0, y - padding)
    x2, y2 = min(width, x + w + padding), min(height, y + h + padding)
    roi = frame[y1:y2, x1:x2]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    background = cv2.GaussianBlur(gray, (0, 0), sigmaX=max(1, 3 * height / 720))
    contrast = cv2.absdiff(gray, background)
    # A lower percentile makes a fixed share of every ROI foreground, so busy
    # footage can turn into a nearly solid mask.  Keep only strong local detail;
    # Canny below still contributes thin dark or low-contrast outlines.
    threshold = max(12, int(np.percentile(contrast, 82)))
    candidate = (contrast > threshold).astype(np.uint8) * 255
    edges = cv2.Canny(gray, 50, 150)
    candidate = cv2.bitwise_or(candidate, edges)
    allowed = np.zeros_like(candidate)
    local = points - np.array([x1, y1])
    cv2.fillPoly(allowed, [local], 255)
    candidate = cv2.bitwise_and(candidate, allowed)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(candidate, 8)
    filtered = np.zeros_like(candidate)
    roi_area = max(1, candidate.size)
    for label in range(1, count):
        area = stats[label, cv2.CC_STAT_AREA]
        component_x = stats[label, cv2.CC_STAT_LEFT]
        component_y = stats[label, cv2.CC_STAT_TOP]
        component_w = stats[label, cv2.CC_STAT_WIDTH]
        component_h = stats[label, cv2.CC_STAT_HEIGHT]
        touches = sum((component_x == 0, component_y == 0,
                       component_x + component_w >= candidate.shape[1],
                       component_y + component_h >= candidate.shape[0]))
        # Outlined captions often become one connected component occupying 35-60%
        # of their tight OCR polygon.  Reject only near-full, border-spanning regions;
        # the previous 35% cap discarded valid dense CJK and bold Latin captions.
        if 2 <= area <= roi_area * 0.80 and not (touches >= 3 and area > roi_area * 0.55):
            filtered[labels == label] = 255
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    filtered = cv2.morphologyEx(filtered, cv2.MORPH_CLOSE, kernel)
    # Edge detectors outline bright and outlined glyphs but can leave their
    # interiors untouched.  Fill only compact external contours; large contours
    # stay unfilled so bridges in a busy background cannot become a solid band.
    contours, _ = cv2.findContours(filtered, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for contour in contours:
        contour_area = cv2.contourArea(contour)
        _cx, _cy, contour_w, contour_h = cv2.boundingRect(contour)
        if (2 <= contour_area <= roi_area * 0.18
                and contour_w * contour_h <= roi_area * 0.24):
            cv2.drawContours(filtered, [contour], -1, 255, thickness=cv2.FILLED)
    output = np.zeros((height, width), dtype=np.uint8)
    output[y1:y2, x1:x2] = filtered
    return output


def vote_masks(masks, tolerance=1):
    import cv2
    import numpy as np
    if not masks:
        raise ValueError("At least one mask is required")
    required = 2 if len(masks) >= 3 else 1
    binary = [(mask > 0).astype(np.uint8) * 255 for mask in masks]
    if required == 1:
        return np.maximum.reduce(binary)
    if tolerance > 0:
        size = tolerance * 2 + 1
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
        expanded = [cv2.dilate(mask, kernel) for mask in binary]
    else:
        expanded = binary
    support = (sum((mask > 0).astype(np.uint8) for mask in expanded) >= required)
    union = np.maximum.reduce(binary)
    # Keep original glyph pixels that have corroborating evidence nearby.  This
    # tolerates codec shimmer, antialiasing and small OCR-box motion without
    # admitting texture that appears in only one reference frame.
    voted = np.where(support & (union > 0), 255, 0).astype(np.uint8)
    return voted


def _nearest_polygon(track, timestamp_ms):
    observations = track.get("observations") or []
    if not observations:
        return None
    return min(observations, key=lambda item: abs(item["timestamp_ms"] - timestamp_ms))["polygon"]


def dynamic_track_mask(frame, track, anchor, timestamp_ms, config):
    """Detect this frame's glyphs while using the track mask only as a loose anchor."""
    import cv2
    import numpy as np

    polygon = _nearest_polygon(track, timestamp_ms)
    if polygon is None:
        return anchor
    height = frame.shape[0]
    candidate = glyph_candidate(frame, polygon, config)
    support_radius = max(1, scaled_px(config.mask_roi_padding_px_720p, height))
    support_kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (support_radius * 2 + 1, support_radius * 2 + 1))
    support = cv2.dilate(anchor, support_kernel)
    # Add solid glyph interiors.  Edge/local-contrast candidates are excellent
    # anchors but may outline a bright, dark, or colored stroke without selecting
    # its flat center.  Color extrema are safe here because they must also fall
    # inside both the current OCR polygon and the dilated temporal anchor.
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    saturation, value = hsv[:, :, 1], hsv[:, :, 2]
    extreme = (((value >= 218) & (saturation <= 105))
               | (value <= 42)
               | ((saturation >= 72) & (value >= 135))).astype(np.uint8) * 255
    allowed = np.zeros_like(anchor)
    cv2.fillPoly(allowed, [np.asarray(polygon, dtype=np.int32)], 255)
    extreme = cv2.bitwise_and(extreme, cv2.bitwise_and(support, allowed))
    supported = cv2.bitwise_and(cv2.bitwise_or(candidate, extreme), support)
    if cv2.countNonZero(supported) < max(8, round(cv2.countNonZero(anchor) * 0.10)):
        supported = anchor.copy()

    points = np.asarray(polygon, dtype=np.int32)
    polygon_area = max(1.0, cv2.contourArea(points))
    max_coverage = 0.72
    dilation = scaled_px(config.mask_dilate_px_720p, height)
    selected = supported
    for radius in range(dilation, 0, -1):
        size = radius * 2 + 1
        grown = cv2.dilate(supported, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size)))
        if cv2.countNonZero(grown) / polygon_area <= max_coverage:
            selected = grown
            break
    # A frame-local cap is deliberately tighter than the static padded-ROI
    # guard.  It prevents changing backgrounds from becoming a solid band.
    if cv2.countNonZero(selected) / polygon_area > max_coverage:
        selected = supported
    return selected


def track_mask(video, track, config):
    import cv2
    import numpy as np
    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise ValueError("Unable to open video for mask generation")
    observations = track["observations"]
    choices = [observations[0], observations[len(observations) // 2], observations[-1]]
    candidates = []
    try:
        for item in choices:
            capture.set(cv2.CAP_PROP_POS_MSEC, item["timestamp_ms"])
            ok, frame = capture.read()
            if ok:
                candidates.append(glyph_candidate(frame, item["polygon"], config))
    finally:
        capture.release()
    if not candidates:
        raise ValueError("Unable to read mask reference frames")
    height = candidates[0].shape[0]
    # This track-level result is only a conservative spatial anchor.  The actual
    # removal mask is rebuilt from every frame by dynamic_track_mask.
    dilation = scaled_px(config.mask_dilate_px_720p, height)
    tolerance = max(1, dilation // 2)
    voted = vote_masks(candidates, tolerance=tolerance)
    polygon = np.asarray(observations[len(observations) // 2]["polygon"], dtype=np.int32)
    polygon_area = max(1.0, cv2.contourArea(polygon))
    # Reject a weak or leaking anchor.  Dilation belongs to the frame-local mask,
    # after the current frame has supplied fresh glyph evidence.
    x, y, w, h = cv2.boundingRect(polygon)
    padding = scaled_px(config.mask_roi_padding_px_720p, height)
    x1, y1 = max(0, x - padding), max(0, y - padding)
    x2, y2 = min(voted.shape[1], x + w + padding), min(voted.shape[0], y + h + padding)
    safe_area = max(1, (x2 - x1) * (y2 - y1))
    selected = voted
    coverage = cv2.countNonZero(selected) / polygon_area
    if coverage < 0.05:
        raise ValueError("GLYPH_MASK_COVERAGE_TOO_LOW")
    if cv2.countNonZero(selected) / safe_area > 0.85:
        raise ValueError("GLYPH_MASK_COVERAGE_TOO_HIGH")
    return selected


def manual_region_mask(width, height, region):
    import numpy as np
    mask = np.zeros((height, width), dtype=np.uint8)
    x1, y1 = round(region.x * width), round(region.y * height)
    x2, y2 = round((region.x + region.w) * width), round((region.y + region.h) * height)
    mask[y1:y2, x1:x2] = 255
    return mask
