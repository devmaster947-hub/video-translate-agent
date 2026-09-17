"""Per-segment synthesis with verified WAV cache; no time alignment here."""
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from .files import write_json, read_json
from .models import Segment
from .state import fingerprint


def dub_segments(segments, directory, provider, voice, config, media):
    tts_dir = directory / "tts"
    tts_dir.mkdir(exist_ok=True)
    result=[]
    for segment in segments:
        if not segment.translated_text:
            raise ValueError("Missing translated text")
        path=tts_dir / f"{segment.id:04d}.wav"
        receipt=path.with_suffix(".json")
        model=config.minimax_model if voice.provider=="minimax" else config.elevenlabs_model
        signature=hashlib.sha256(json.dumps({"text":segment.translated_text,"provider":voice.provider,
            "voice_id":voice.voice_id,"model":model,"host":config.minimax_host if voice.provider=="minimax" else None,
            "target":voice.language,"format":"pcm_s16le/48000/2","version":1},sort_keys=True).encode()).hexdigest()
        cached=read_json(receipt) if receipt.exists() else {}
        valid=path.exists() and cached.get("signature")==signature and cached.get("sha256")==fingerprint(path)
        if not valid:
            original=tts_dir / f".raw-{uuid4().hex}.audio"
            normalized=tts_dir / f".normalized-{uuid4().hex}.wav"
            try:
                provider.synthesize(segment.translated_text,voice.voice_id,voice.language,original)
                media.normalize_audio(original,normalized)
                os.replace(normalized,path)
                write_json(receipt,{"signature":signature,"sha256":fingerprint(path)})
            finally:
                original.unlink(missing_ok=True)
                normalized.unlink(missing_ok=True)
        info=media.probe(path)
        stream=info["streams"][0]
        if len(info["streams"])!=1 or (stream.get("codec_name"),stream.get("sample_rate"),stream.get("channels"))!=("pcm_s16le","48000",2):
            raise ValueError("Invalid normalized TTS")
        result.append(Segment.model_validate(segment.model_dump() | {
            "tts_file":path.relative_to(directory).as_posix(),"tts_duration_ms":info["duration_ms"]}))
    return result
