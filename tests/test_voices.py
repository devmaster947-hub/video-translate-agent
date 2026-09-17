import pytest
from types import SimpleNamespace as NS
from video_translate.tts.elevenlabs import ElevenLabsTTS, parse_voices
from video_translate.tts.minimax import parse_voices as parse_mini, parse_audio
from video_translate.config import Config
from video_translate.network import ProviderError


def test_elevenlabs_same_name_and_pagination(tmp_path):
    calls=[]
    class Session:
        def request(self, method, url, **kwargs):
            calls.append(kwargs)
            page=len(calls)
            return NS(status_code=200,json=lambda:{"voices":[{"voice_id":str(page),"name":"same"}],
                "has_more":page==1,"next_page_token":"next"})
    provider=ElevenLabsTTS(Config(runtime_root=tmp_path,elevenlabs_api_key="test"),Session())
    voices=provider.list_voices("en")
    assert [v.voice_id for v in voices]==["1","2"]
    assert calls[1]["params"]["next_page_token"]=="next"


def test_minimax_intersects_real_ids_and_language_catalog():
    data={"base_resp":{"status_code":0},"system_voice":[{"voice_id":"one"},{"voice_id":"foreign"}]}
    voices=parse_mini(data,{"first":"one","not available":"two"},"en","api.minimax.io")
    assert [v.voice_id for v in voices]==["one"]


@pytest.mark.parametrize("payload", [{}, {"base_resp":{"status_code":1004}},
    {"base_resp":{"status_code":0},"data":{"audio":"badhex"}}])
def test_minimax_error_is_never_audio(payload):
    with pytest.raises(ProviderError): parse_audio(payload)


def test_audio_hex():
    assert parse_audio({"base_resp":{"status_code":0},"data":{"audio":"52494646"}})==b"RIFF"


def test_elevenlabs_bad_list():
    with pytest.raises(ProviderError): parse_voices({"voices":[{}]},"en")
