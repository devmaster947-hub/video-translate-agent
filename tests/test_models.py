import pytest
from pydantic import ValidationError

from video_translate.models import Artifact, Manifest, Segment, Voice


def test_segment_roundtrip_and_immutable():
    segment = Segment(id=1, start_ms=0, end_ms=1200, raw_text="你好")
    assert Segment.model_validate_json(segment.model_dump_json()) == segment
    with pytest.raises(ValidationError):
        segment.start_ms = 10


@pytest.mark.parametrize("changes", [
    {"id": True}, {"id": 0}, {"start_ms": 1.5}, {"start_ms": -1},
    {"end_ms": 0}, {"end_ms": "1200"}, {"extra": 1},
    {"final_start_ms": 10}, {"final_start_ms": 10, "final_end_ms": 10},
    {"tts_file": "tts/1.wav"}, {"tts_file": "../1.wav", "tts_duration_ms": 100},
    {"tts_file": "C:\\video.wav", "tts_duration_ms": 100},
])
def test_segment_rejects_invalid_contract(changes):
    with pytest.raises(ValidationError):
        Segment(**({"id": 1, "start_ms": 0, "end_ms": 1200, "raw_text": "hello"} | changes))


def test_voice_identity_not_name():
    first = Voice(provider="minimax", voice_id="one", voice_name="same", display_index=1)
    second = Voice(provider="elevenlabs", voice_id="two", voice_name="same", display_index=2)
    assert first.voice_name == second.voice_name and first != second


@pytest.mark.parametrize("path", ["../x", "..\\x", "C:x", "C:\\x", "/x", "\\\\host\\share\\x", "."])
def test_artifact_rejects_escaping_paths(path):
    with pytest.raises(ValidationError):
        Artifact(path=path, size_bytes=1, sha256="a" * 64)


def test_manifest_rejects_secrets_and_bad_state(job):
    _, manifest = job
    for changes in ({"api_key": "secret"}, {"phase": "DONE"},
                    {"phase": "FAILED"}, {"error": "Bearer secret"}):
        with pytest.raises(ValidationError):
            Manifest.model_validate(manifest.model_dump() | changes)
