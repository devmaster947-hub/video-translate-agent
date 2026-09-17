import cv2
import numpy as np
import pytest

from video_translate.config import CleanupConfig
from video_translate.overlay_mask import dynamic_track_mask, glyph_candidate, vote_masks


@pytest.mark.parametrize("color", [(255, 255, 255), (0, 220, 255), (165, 165, 165)])
def test_glyph_mask_does_not_fill_ocr_rectangle(color):
    frame = np.full((180, 320, 3), 90, np.uint8)
    cv2.putText(frame, "TEXT", (85, 110), cv2.FONT_HERSHEY_SIMPLEX, 1,
                (0, 0, 0), 5, cv2.LINE_AA)
    cv2.putText(frame, "TEXT", (85, 110), cv2.FONT_HERSHEY_SIMPLEX, 1,
                color, 2, cv2.LINE_AA)
    polygon = [[75, 75], [245, 75], [245, 125], [75, 125]]
    mask = glyph_candidate(frame, polygon, CleanupConfig())
    assert np.count_nonzero(mask) > 20
    assert np.count_nonzero(mask[75:125, 75:245]) < 170 * 50 * .45


def test_three_frame_vote_rejects_single_frame_texture():
    stable = np.zeros((20, 20), np.uint8)
    stable[5:8, 5:8] = 255
    noise = stable.copy()
    noise[15:18, 15:18] = 255
    voted = vote_masks([stable, stable, noise])
    assert voted[6, 6] == 255 and voted[16, 16] == 0


def test_three_frame_vote_tolerates_small_glyph_motion():
    masks = []
    for shift in (-2, 0, 2):
        mask = np.zeros((40, 60), np.uint8)
        mask[12:24, 20 + shift:32 + shift] = 255
        masks.append(mask)
    masks[0][32:36, 48:52] = 255
    voted = vote_masks(masks, tolerance=2)
    assert np.count_nonzero(voted[12:24, 18:34]) >= 12 * 12
    assert np.count_nonzero(voted[32:36, 48:52]) == 0


def test_dense_outlined_caption_is_not_discarded_as_large_component():
    frame = np.full((180, 420, 3), 95, np.uint8)
    cv2.putText(frame, "BOLD CAPTION", (45, 112), cv2.FONT_HERSHEY_SIMPLEX,
                1.15, (0, 0, 0), 8, cv2.LINE_AA)
    cv2.putText(frame, "BOLD CAPTION", (45, 112), cv2.FONT_HERSHEY_SIMPLEX,
                1.15, (255, 255, 255), 3, cv2.LINE_AA)
    polygon = [[35, 68], [390, 68], [390, 128], [35, 128]]
    mask = glyph_candidate(frame, polygon, CleanupConfig())
    coverage = np.count_nonzero(mask[68:128, 35:390]) / (60 * 355)
    assert 0.08 < coverage < 0.70


def test_dynamic_mask_tracks_current_glyphs_without_filling_caption_band():
    config = CleanupConfig()
    frame = np.full((180, 420, 3), 95, np.uint8)
    polygon = [[35, 68], [390, 68], [390, 128], [35, 128]]
    cv2.putText(frame, "FRAME TEXT", (65, 112), cv2.FONT_HERSHEY_SIMPLEX,
                1.15, (0, 0, 0), 8, cv2.LINE_AA)
    cv2.putText(frame, "FRAME TEXT", (65, 112), cv2.FONT_HERSHEY_SIMPLEX,
                1.15, (255, 255, 255), 3, cv2.LINE_AA)
    anchor = glyph_candidate(frame, polygon, config)
    track = {"observations": [{"timestamp_ms": 0, "polygon": polygon}]}
    mask = dynamic_track_mask(frame, track, anchor, 0, config)
    coverage = np.count_nonzero(mask) / cv2.contourArea(np.asarray(polygon, np.int32))
    white_glyph = np.all(frame >= 245, axis=2)
    assert 0.08 < coverage <= 1.35
    assert np.count_nonzero((mask > 0) & white_glyph) / np.count_nonzero(white_glyph) > 0.90
