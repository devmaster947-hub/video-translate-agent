from pathlib import Path
from typing import Protocol
from ..models import Voice


class TTSProvider(Protocol):
    def list_voices(self, target: str) -> list[Voice]: ...
    def synthesize(self, text: str, voice_id: str, target: str, output: Path) -> Path: ...
