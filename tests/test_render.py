from pathlib import Path
import pytest
from video_translate.models import Manifest, Segment
from video_translate.state import fingerprint
from video_translate.render import render_video, verify_output


@pytest.mark.media
def test_windows_hard_subtitle_all_source_audio_removed(source_video,real_media,config,tmp_path):
    directory=source_video.parent
    (directory/"aligned").mkdir()
    before=fingerprint(source_video)
    real_media.video_without_audio(source_video,directory/"aligned/video.mp4")
    # This fixture is generated media; production render always uses dubbed target.wav.
    real_media.normalize_audio(source_video,directory/"target.wav")
    manifest=Manifest(job_id="a"*32,input_video=str(source_video),input_fingerprint=before,config_snapshot=config.snapshot(),
                      target_language="zh-Hans")
    segments=[Segment(id=1,start_ms=0,end_ms=500,raw_text="test",translated_text="中文测试",
                      final_start_ms=0,final_end_ms=500)]
    output=render_video(manifest,segments,directory,real_media)
    info=verify_output(output,real_media,600)
    assert len(info["streams"])==2 and fingerprint(source_video)==before
    assert (directory/"target.srt").exists() and (directory/"target.ass").exists()
