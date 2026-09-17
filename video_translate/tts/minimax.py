"""MiniMax t2a_v2; catalog extracted from pinned pyVideoTrans voicejson (GPL-3.0)."""
from pathlib import Path
from urllib.parse import urlsplit
from ..config import PROJECT_ROOT
from ..files import read_json
from ..languages import language
from ..models import Voice
from ..network import request, json_response, ProviderError


def check_status(data):
    if not isinstance(data, dict) or data.get("base_resp", {}).get("status_code") != 0:
        raise ProviderError("MINIMAX_BUSINESS_ERROR")


def parse_voices(data, catalog, target, host):
    check_status(data)
    rows = data.get("system_voice")
    if not isinstance(rows, list):
        raise ProviderError("INVALID_VOICE_RESPONSE")
    remote_ids = {row["voice_id"] for row in rows if isinstance(row, dict) and isinstance(row.get("voice_id"), str)}
    return [Voice(provider="minimax",voice_id=voice_id,voice_name=name,language=target,host=host,display_index=i+1)
            for i, (name, voice_id) in enumerate(catalog.items()) if voice_id in remote_ids]


def parse_audio(data):
    check_status(data)
    try:
        encoded = data["data"]["audio"]
        if not isinstance(encoded, str) or not encoded:
            raise ValueError()
        content = bytes.fromhex(encoded)
    except (KeyError, TypeError, ValueError):
        raise ProviderError("INVALID_MINIMAX_AUDIO") from None
    return content


class MiniMaxTTS:
    def __init__(self, config, session=None):
        self.config, self.session = config, session

    def _headers(self):
        if not self.config.minimax_api_key:
            raise ProviderError("MINIMAX_KEY_MISSING")
        return {"Authorization":"Bearer " + self.config.minimax_api_key.get_secret_value()}

    def list_voices(self, target):
        host = urlsplit(self.config.minimax_host).hostname
        catalog = read_json(PROJECT_ROOT / "config/minimax_voices.json").get(host, {}).get(language(target).minimax, {})
        if not catalog:
            raise ProviderError("MINIMAX_LANGUAGE_CATALOG_UNAVAILABLE")
        data = json_response(request("POST", self.config.minimax_host + "/v1/get_voice",
            session=self.session, headers=self._headers(), json={"voice_type":"system"}))
        return parse_voices(data, catalog, target, host)

    def synthesize(self, text, voice_id, target, output: Path):
        data = json_response(request("POST", self.config.minimax_host + "/v1/t2a_v2",
            session=self.session, attempts=1, headers=self._headers(), json={
                "model":self.config.minimax_model,"text":text,"stream":False,
                "voice_setting":{"voice_id":voice_id,"speed":1.0,"vol":1.0,"pitch":0},
                "language_boost":language(target).boost,
                "audio_setting":{"sample_rate":44100,"format":"wav","channel":1}}))
        output.write_bytes(parse_audio(data))
        return output
