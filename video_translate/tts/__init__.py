"""Only MiniMax and ElevenLabs providers are supported."""
from .minimax import MiniMaxTTS
from .elevenlabs import ElevenLabsTTS


def providers(config):
    available = {}
    if config.minimax_api_key:
        available["minimax"] = MiniMaxTTS(config)
    if config.elevenlabs_api_key:
        available["elevenlabs"] = ElevenLabsTTS(config)
    return available
