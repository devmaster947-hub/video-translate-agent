import wave
import pytest
from video_translate.models import Segment, Voice
from video_translate.dubbing import dub_segments


@pytest.mark.media
def test_each_segment_normalized_and_cached(tmp_path, config, real_media):
    calls=[]
    class Provider:
        def synthesize(self,text,voice_id,target,output):
            calls.append(text)
            with wave.open(str(output),"wb") as wav:
                wav.setparams((1,2,16000,0,"NONE","not compressed"))
                wav.writeframes(b"\x01\x01"*8000)
    voice=Voice(provider="minimax",voice_id="fixture",voice_name="fixture",language="en",display_index=1)
    segments=[Segment(id=i+1,start_ms=i*1000,end_ms=i*1000+900,raw_text="x",translated_text=f"text {i}") for i in range(2)]
    first=dub_segments(segments,tmp_path,Provider(),voice,config,real_media)
    second=dub_segments(segments,tmp_path,Provider(),voice,config,real_media)
    assert len(calls)==2 and first==second
    assert [s.tts_file for s in first]==["tts/0001.wav","tts/0002.wav"]
    assert all(s.tts_duration_ms==500 for s in first)
    (tmp_path / first[0].tts_file).write_bytes(b"corrupt")
    dub_segments(segments,tmp_path,Provider(),voice,config,real_media)
    assert len(calls)==3
