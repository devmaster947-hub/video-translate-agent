import math
import wave
import pytest
from video_translate.alignment import atempo_chain, align_segments
from video_translate.models import Segment


def test_atempo_over_two():
    assert atempo_chain(3)=="atempo=2,atempo=1.5"
    for rate in (2,3,8,19):
        factors=[float(part.split("=")[1]) for part in atempo_chain(rate).split(",")]
        assert max(factors)<=2 and math.prod(factors)==rate


@pytest.mark.media
def test_real_alignment_executes_remote_plan_without_local_decision(tmp_path,real_media):
    directory=tmp_path/"对齐 空格 ' 目录"
    directory.mkdir()
    video=directory/"原画面.mp4"
    real_media.run(real_media.config.ffmpeg,["-v","error","-nostdin","-y","-f","lavfi","-i",
        "testsrc2=size=160x120:rate=25:duration=3.2","-c:v","libx264",str(video)])
    segments=[]
    for i,duration in enumerate((4000,400,1100)):
        path=directory/f"{i}.wav"
        with wave.open(str(path),"wb") as wav:
            wav.setparams((2,2,48000,0,"NONE","not compressed"))
            import struct
            wav.writeframes(b"".join(struct.pack("<hh",int(2000*math.sin(j*.1)),int(2000*math.sin(j*.1)))
                                     for j in range(duration*48)))
        segments.append(Segment(id=i+1,start_ms=200+i*1000,end_ms=1000+i*1000,raw_text="test",
                                 translated_text="test",tts_file=path.name,tts_duration_ms=duration))

    def canned_policy(payload):
        values=[]
        for i,s in enumerate(payload["segments"]):
            end = payload["segments"][i+1]["start_ms"] if i+1 < len(payload["segments"]) else payload["video_duration_ms"]
            source=end-s["start_ms"]
            target=max(source,s["tts_duration_ms"])
            values.append({"segment_id":s["id"],"source_start_ms":s["start_ms"],"source_end_ms":end,
                "source_duration_ms":source,"tts_duration_ms":s["tts_duration_ms"],"target_ms":target,
                "audio_speed":1.0,"video_pts":max(1,target/source)})
        return {"success":True,"alignment_plan":{"leading_ms":payload["segments"][0]["start_ms"],"slots":values}}

    result=align_segments(segments,video,directory,real_media,policy_requester=canned_policy)
    assert all(a.final_end_ms<=b.final_start_ms for a,b in zip(result,result[1:]))
    assert (directory/"aligned/video.mp4").is_file()
    with wave.open(str(directory/"target.wav")) as wav:
        from video_translate.files import read_json
        report=read_json(directory/"alignment_result.json")
        assert wav.getnframes()==report["total_samples"]
