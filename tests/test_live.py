"""Explicit live checks only. No default paid voice or external translation."""
import os
import pytest
from video_translate.config import load_config
from video_translate.tts import providers

pytestmark=pytest.mark.live

def test_live_provider_voice_list():
    name=os.environ.get("VIDEO_TRANSLATE_LIVE_PROVIDER")
    if name not in ("minimax","elevenlabs"):
        pytest.skip("Set VIDEO_TRANSLATE_LIVE_PROVIDER explicitly")
    configured=providers(load_config())
    assert name in configured
    assert configured[name].list_voices(os.environ.get("VIDEO_TRANSLATE_LIVE_TARGET","en"))
