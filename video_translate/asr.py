"""Faster Whisper only. Reference: pyVideoTrans stt_faster.py at ad0b8bd8."""
from pathlib import Path
from .config import ASRConfig, anchored_path
from .resegment import resegment


class ASRError(RuntimeError):
    pass


def transcribe(audio: Path, source: str, config: ASRConfig, model_factory=None):
    try:
        if model_factory is None:
            from faster_whisper import WhisperModel
            model_factory = WhisperModel
        device, compute = config.device, config.compute_type
        if device == "auto":
            import ctranslate2
            device = "cuda" if ctranslate2.get_cuda_device_count() else "cpu"
        if compute == "auto":
            compute = "float16" if device == "cuda" else "int8"
        model_name = config.model
        if "/" in model_name or "\\" in model_name:
            candidate = anchored_path(model_name)
            if candidate.exists():
                model_name = str(candidate)
        model = model_factory(model_name, device=device, compute_type=compute)
        results, info = model.transcribe(str(audio), language=None if source == "auto" else source,
            beam_size=config.beam_size, vad_filter=config.vad, word_timestamps=config.word_timestamps,
            vad_parameters={"min_silence_duration_ms": 500}, condition_on_previous_text=False)
        raws = [{"text": item.text, "start": item.start, "end": item.end,
                 "words": [{"word": word.word, "start": word.start, "end": word.end}
                           for word in (item.words or [])]} for item in results]
        segments = resegment(raws, info.language, config.min_segment_ms, config.max_segment_ms)
        if not segments:
            raise ASRError("NO_SPEECH")
        return segments, info.language, {"device": device, "compute_type": compute, "model": config.model}
    except ASRError:
        raise
    except Exception:
        if config.device == "auto" and locals().get("device") == "cuda":
            # Auto means a deterministic local fallback is allowed; explicit CUDA still fails.
            segments, detected, runtime = transcribe(audio, source,
                config.model_copy(update={"device":"cpu", "compute_type":"auto"}), model_factory)
            return segments, detected, runtime | {"fallback":"cuda_to_cpu"}
        raise ASRError("ASR_EXECUTION_FAILED") from None
