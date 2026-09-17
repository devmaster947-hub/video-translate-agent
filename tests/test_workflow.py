from pathlib import Path
from types import SimpleNamespace as NS
import math
import struct
import wave
import pytest

from video_translate.config import Config, CleanupConfig
from video_translate.models import Segment, Voice, Phase, AlignmentPlan, AlignmentSlot
from video_translate.files import read_json, write_json
from video_translate.state import JobStore, fingerprint
from video_translate.workflow import transcribe_job,clean_job,polish_job,local_translate_job,voices_job,dub_job,align_job,render_job,next_action


@pytest.mark.media
def test_mock_services_real_media_pipeline_and_resume(tmp_path,source_video,real_media,monkeypatch):
    config=Config(runtime_root=tmp_path,minimax_api_key="fixture-key",cleanup=CleanupConfig(enabled=False))
    store=JobStore(tmp_path)
    manifest=store.create(source_video,config.snapshot())
    job_id=manifest.job_id
    directory=store.directory(job_id)
    original=fingerprint(source_video)
    monkeypatch.setattr("video_translate.workflow.transcribe",lambda *args: (
        [Segment(id=1,start_ms=0,end_ms=500,raw_text="Hello world")],"en",{"device":"test"}))
    transcribe_job(store,job_id,config,real_media,"en","zh-CN")
    clean_job(store,job_id,config,real_media)
    write_json(directory/"segments_polished.json",read_json(directory/"segments_raw.json"))
    polish_job(store,job_id)
    translation=read_json(directory/"segments_translation.json")
    translation[0]["text"]="你好世界"
    write_json(directory/"segments_translation.json",translation)
    local_translate_job(store,job_id)
    calls=[]
    class Provider:
        def list_voices(self,target):
            return [Voice(provider="minimax",voice_id="test-id",voice_name="test voice",language=target,display_index=1)]
        def synthesize(self,text,voice_id,target,output):
            calls.append(text)
            with wave.open(str(output),"wb") as wav:
                wav.setparams((1,2,16000,0,"NONE","not compressed"))
                wav.writeframes(b"".join(struct.pack("<h",int(1000*math.sin(i*.15))) for i in range(16000)))
    providers={"minimax":Provider()}
    voices_job(store,job_id,config,providers)
    assert next_action(store.load(job_id))=="SELECT_VOICE"
    dub_job(store,job_id,config,real_media,"minimax","test-id",providers)
    # New store simulates a new Agent turn; no voice re-selection and no paid synthesis repeat.
    store=JobStore(tmp_path)
    dub_job(store,job_id,config,real_media,available=providers)
    assert len(calls)==1
    # Remote policy is represented by a fixed returned plan; this test validates only local execution.
    monkeypatch.setattr("video_translate.policy_client.resolve_alignment_policy", lambda **kwargs: AlignmentPlan(
        leading_ms=0, slots=[AlignmentSlot(segment_id=1, source_start_ms=0, source_end_ms=600,
            source_duration_ms=600, tts_duration_ms=1000, target_ms=800, audio_speed=1.25, video_pts=4/3)]))
    align_job(store,job_id,config,real_media)
    result=render_job(store,job_id,config,real_media)
    assert result.phase==Phase.DONE and store.verify(result)==[]
    assert fingerprint(source_video)==original
    assert Path(result.output_file).exists()
    assert render_job(store,job_id,config,real_media)==result
