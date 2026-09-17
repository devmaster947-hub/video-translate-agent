import json
from pathlib import Path

import pytest

from video_translate.models import Phase
from video_translate.state import JobStore, StateError


def test_create_and_reload_from_new_store(job):
    store, manifest = job
    restored = JobStore(store.root.parent).load(manifest.job_id)
    assert restored == manifest
    assert restored.phase == Phase.CREATED
    assert store.verify(restored) == []


def test_transition_fail_and_resume_preserves_artifacts(job):
    store, manifest = job
    artifact = store.directory(manifest.job_id) / "source.wav"
    artifact.write_bytes(b"verified fixture")
    store.record_artifact(manifest.job_id, "audio", "source.wav")
    store.transition(manifest.job_id, Phase.LANG_CONFIRMED, source_language="auto", target_language="en")
    failed = store.fail(manifest.job_id, Phase.TRANSCRIBED, "MODEL_LOAD_FAILED")
    assert failed.phase == Phase.FAILED and failed.last_successful_phase == Phase.LANG_CONFIRMED
    restored = JobStore(store.root.parent)
    done = restored.transition(manifest.job_id, Phase.TRANSCRIBED)
    assert done.error is None and done.failed_stage is None and "audio" in done.artifacts
    assert artifact.read_bytes() == b"verified fixture"


def test_illegal_transitions_do_not_write(job):
    store, manifest = job
    original = (store.directory(manifest.job_id) / "manifest.json").read_bytes()
    for target in (Phase.DONE, Phase.CREATED, Phase.FAILED):
        with pytest.raises(StateError):
            store.transition(manifest.job_id, target)
    with pytest.raises(StateError):
        store.transition(manifest.job_id, Phase.LANG_CONFIRMED, input_video="other")
    assert (store.directory(manifest.job_id) / "manifest.json").read_bytes() == original


def test_atomic_replace_failure_leaves_old_manifest(job, monkeypatch):
    store, manifest = job
    path = store.directory(manifest.job_id) / "manifest.json"
    before = path.read_bytes()

    def fail_replace(*args):
        raise OSError("injected publication failure")

    monkeypatch.setattr("video_translate.state.os.replace", fail_replace)
    with pytest.raises(OSError):
        store.transition(manifest.job_id, Phase.LANG_CONFIRMED, source_language="zh", target_language="en")
    assert path.read_bytes() == before
    assert list(path.parent.glob(".manifest-*.tmp")) == []
    assert json.loads(path.read_text())["phase"] == "CREATED"


def test_single_writer_lock_and_release(job):
    store, manifest = job
    with store.lock(manifest.job_id):
        with pytest.raises(StateError):
            with store.lock(manifest.job_id):
                pytest.fail("A second writer acquired the lock")
    with store.lock(manifest.job_id):
        pass


def test_tampered_input_and_artifact_are_detected(job):
    store, manifest = job
    path = store.directory(manifest.job_id) / "source.wav"
    path.write_bytes(b"original")
    manifest = store.record_artifact(manifest.job_id, "audio", "source.wav")
    path.write_bytes(b"tampered")
    Path(manifest.input_video).write_bytes(b"changed")
    assert store.verify(manifest) == ["INPUT_CHANGED_OR_MISSING", "ARTIFACT_CHANGED_OR_MISSING:audio"]


def test_job_and_artifact_paths_cannot_escape(job):
    store, manifest = job
    with pytest.raises(StateError):
        store.load("../anything")
    with pytest.raises(ValueError):
        store.record_artifact(manifest.job_id, "audio", "..\\outside.wav")


def test_manifest_does_not_save_key(config, tmp_path):
    from video_translate.config import load_config
    source = tmp_path / "x.mp4"
    source.write_bytes(b"fixture")
    runtime = load_config(runtime_root=tmp_path, environ={"ELEVENLABS_API_KEY": "private-test-key"})
    store = JobStore(tmp_path)
    manifest = store.create(source, runtime.snapshot())
    assert "private-test-key" not in (store.directory(manifest.job_id) / "manifest.json").read_text()


def test_complete_state_order_and_terminal_state(job):
    from video_translate.state import ORDER
    store, manifest = job
    for phase in ORDER[1:]:
        manifest = store.transition(manifest.job_id, phase, source_language="zh", target_language="en")
        assert manifest.phase == phase
    with pytest.raises(StateError):
        store.fail(manifest.job_id, Phase.DONE, "FAILED_RENDER")
    with pytest.raises(StateError):
        store.transition(manifest.job_id, Phase.CREATED)


def test_confirmed_choices_cannot_change(job):
    store, manifest = job
    store.transition(manifest.job_id, Phase.LANG_CONFIRMED, source_language="zh", target_language="en")
    with pytest.raises(StateError):
        store.transition(manifest.job_id, Phase.TRANSCRIBED, target_language="fr")


def test_language_transition_requires_choices(job):
    from pydantic import ValidationError
    store, manifest = job
    with pytest.raises(ValidationError):
        store.transition(manifest.job_id, Phase.LANG_CONFIRMED)
