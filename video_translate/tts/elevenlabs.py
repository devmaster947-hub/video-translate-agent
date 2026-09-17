"""ElevenLabs HTTP adapter, pagination and ID identity; no SDK/global config."""
from pathlib import Path
from urllib.parse import quote
from ..models import Voice
from ..network import request, json_response, ProviderError
from ..languages import language


def parse_voices(payload, target):
    if not isinstance(payload, dict) or not isinstance(payload.get("voices"), list):
        raise ProviderError("INVALID_VOICE_RESPONSE")
    result = []
    for item in payload["voices"]:
        if not isinstance(item, dict) or not item.get("voice_id") or not item.get("name"):
            raise ProviderError("INVALID_VOICE_ITEM")
        result.append(Voice(provider="elevenlabs", voice_id=item["voice_id"], voice_name=item["name"],
                            language=target, display_index=len(result)+1))
    return result


class ElevenLabsTTS:
    def __init__(self, config, session=None):
        self.config, self.session = config, session

    def _headers(self):
        if not self.config.elevenlabs_api_key:
            raise ProviderError("ELEVENLABS_KEY_MISSING")
        return {"xi-api-key":self.config.elevenlabs_api_key.get_secret_value()}

    def list_voices(self, target):
        language(target)
        voices, tokens = {}, set()
        token = None
        while True:
            params = {"page_size":100}
            if token:
                params["next_page_token"] = token
            data = json_response(request("GET", "https://api.elevenlabs.io/v2/voices", session=self.session,
                                         headers=self._headers(), params=params))
            for voice in parse_voices(data, target):
                voices[voice.voice_id] = voice
            if not data.get("has_more", False):
                break
            token = data.get("next_page_token")
            if not token or token in tokens:
                raise ProviderError("INVALID_VOICE_PAGINATION")
            tokens.add(token)
        return list(voices.values())

    def synthesize(self, text, voice_id, target, output: Path):
        language(target)
        response = request("POST", "https://api.elevenlabs.io/v1/text-to-speech/" + quote(voice_id, safe=""),
            session=self.session, headers=self._headers(), attempts=1,
            params={"output_format":"mp3_44100_128"},
            json={"text":text,"model_id":self.config.elevenlabs_model,
                  "voice_settings":{"speed":1.0,"stability":0.5,"similarity_boost":0.75}})
        if not response.content:
            raise ProviderError("EMPTY_TTS_AUDIO")
        output.write_bytes(response.content)
        return output
