"""Stage transactions: verify inputs, preserve completed artifacts, fail closed."""
from pathlib import Path
from types import SimpleNamespace

from .asr import transcribe
from .config import Config
from .files import read_json, write_json, save_segments, load_segments
from .languages import language, source_code
from .models import Artifact, Phase
from .polish import projection, validate_polish
from .translation import projection as translation_projection, validate_translation
from .state import ORDER, JobStore, StateError, fingerprint


def job_config(manifest, current):
    return Config.model_validate(manifest.config_snapshot.model_dump() | {
        "minimax_api_key": current.minimax_api_key, "elevenlabs_api_key": current.elevenlabs_api_key})


def commit(store, job_id, target, paths, **updates):
    manifest = store.load(job_id)
    artifacts = dict(manifest.artifacts)
    for name, relative in paths.items():
        path = store.artifact_path(job_id, relative)
        artifacts[name] = Artifact(path=relative, sha256=fingerprint(path), size_bytes=path.stat().st_size)
    return store.transition(job_id, target, artifacts=artifacts, **updates)


def stage(store, job_id, target, operation):
    with store.execution(job_id):
        manifest = store.load(job_id)
        if store.verify(manifest):
            raise StateError("Job input or committed artifacts changed")
        if ORDER.index(manifest.last_successful_phase) >= ORDER.index(target):
            return manifest
        if ORDER[ORDER.index(manifest.last_successful_phase) + 1] != target:
            raise StateError("Previous stage is incomplete")
        try:
            return operation(manifest)
        except Exception as exc:
            store.fail(job_id, target, getattr(exc, "code", target.value + "_FAILED"))
            raise


def transcribe_job(store, job_id, config, media, source, target):
    source, target = source_code(source), language(target).code
    with store.execution(job_id):
        manifest = store.load(job_id)
        if manifest.last_successful_phase == Phase.CREATED:
            store.transition(job_id, Phase.LANG_CONFIRMED, source_language=source, target_language=target)
        elif source != manifest.source_language or target != manifest.target_language:
            raise StateError("Language choices already fixed")

    def operation(manifest):
        cfg = job_config(manifest, config)
        directory = store.directory(job_id)
        audio = directory / "source.wav"
        if audio.exists():
            media.probe(audio)
        else:
            media.extract_audio(Path(manifest.input_video), audio)
        store.record_artifact(job_id,"source_audio","source.wav")
        segments, detected, runtime = transcribe(audio, manifest.source_language, cfg.asr)
        duration = media.require_video(Path(manifest.input_video))["duration_ms"]
        if any(item.end_ms > duration or item.start_ms >= duration for item in segments):
            raise ValueError("ASR timestamps exceed video")
        write_json(directory / "segments_raw.json", projection(segments))
        write_json(directory / "asr_runtime.json", runtime)
        return commit(store, job_id, Phase.TRANSCRIBED,
                      {"raw": "segments_raw.json", "source_audio": "source.wav", "asr_runtime": "asr_runtime.json"},
                      detected_source_language=detected)
    return stage(store, job_id, Phase.TRANSCRIBED, operation)


def clean_job(store, job_id, config, media, ocr_engine=None):
    from .cleanup import clean_video
    def operation(manifest):
        directory = store.directory(job_id)
        segments = [SimpleNamespace(**item) for item in read_json(directory / "segments_raw.json")]
        clean_video(Path(manifest.input_video), directory, segments,
                    manifest.config_snapshot.cleanup, media, ocr_engine=ocr_engine, job_id=job_id)
        paths = {"cleaned_video": "clean/video.mp4", "overlay_analysis": "overlay_analysis.json",
                 "subtitle_layout": "subtitle_layout.json", "cleanup_report": "cleanup_report.json"}
        for path in sorted((directory / "masks").glob("*.png")):
            paths[f"cleanup_mask_{path.stem}"] = path.relative_to(directory).as_posix()
        for name in ("cleanup_before.png", "cleanup_mask.png", "cleanup_after.png"):
            path = directory / "preview" / name
            if path.exists():
                paths[f"preview_{path.stem}"] = path.relative_to(directory).as_posix()
        return commit(store, job_id, Phase.CLEANED, paths)
    return stage(store, job_id, Phase.CLEANED, operation)


def polish_job(store, job_id):
    def operation(manifest):
        directory = store.directory(job_id)
        segments = validate_polish(read_json(directory / "segments_raw.json"),
                                   read_json(directory / "segments_polished.json"))
        save_segments(directory / "segments_polished_validated.json", segments)
        write_json(directory / "segments_translation.json", translation_projection(segments))
        return commit(store, job_id, Phase.POLISHED,
                      {"polished": "segments_polished_validated.json", "polish_edit": "segments_polished.json"})
    return stage(store, job_id, Phase.POLISHED, operation)


def local_translate_job(store, job_id):
    def operation(manifest):
        directory = store.directory(job_id)
        segments = load_segments(directory / "segments_polished_validated.json")
        result = validate_translation(segments, read_json(directory / "segments_translation.json"))
        save_segments(directory / "segments_translated.json", result)
        return commit(store, job_id, Phase.TRANSLATED,
                      {"translated": "segments_translated.json",
                       "translation_edit": "segments_translation.json"})
    return stage(store, job_id, Phase.TRANSLATED, operation)


def voices_job(store, job_id, config, available=None, provider_name=None):
    from .tts import providers
    from .network import ProviderError
    def operation(manifest):
        configured = providers(job_config(manifest, config)) if available is None else available
        if provider_name:
            configured = ({provider_name: configured[provider_name]}
                          if provider_name in configured else {})
            if not configured:
                raise ProviderError("SELECTED_PROVIDER_UNAVAILABLE")
        voices, errors = [], {}
        for name, provider in configured.items():
            try:
                found = provider.list_voices(manifest.target_language)
                if not found:
                    raise ProviderError("NO_VOICES_AVAILABLE")
                voices.extend(found)
            except ProviderError as exc:
                errors[name] = str(exc)
        if not voices:
            write_json(store.directory(job_id) / "voice_errors.json", errors)
            raise ProviderError("NO_AVAILABLE_TTS_PROVIDER")
        result = [voice.model_dump() | {"display_index":i+1} for i, voice in enumerate(voices)]
        write_json(store.directory(job_id) / "voices.json", {"voices":result,"unavailable":errors})
        return commit(store, job_id, Phase.WAITING_VOICE, {"voices":"voices.json"})
    return stage(store, job_id, Phase.WAITING_VOICE, operation)


def dub_job(store, job_id, config, media, provider_name=None, voice_id=None, available=None):
    from .tts import providers
    from .models import Voice
    from .dubbing import dub_segments
    from .network import ProviderError
    def operation(manifest):
        cfg=job_config(manifest,config)
        directory=store.directory(job_id)
        name=provider_name or manifest.tts_provider
        selected_id=voice_id or manifest.voice_id
        matches=[Voice.model_validate(v) for v in read_json(directory / "voices.json")["voices"]
                 if v["provider"]==name and v["voice_id"]==selected_id]
        if len(matches)!=1:
            raise ValueError("Select an exact provider/voice ID from the job voice list")
        voice=matches[0]
        store.choose_voice(job_id,name,selected_id,voice.voice_name)
        configured=providers(cfg) if available is None else available
        if name not in configured:
            raise ProviderError("SELECTED_PROVIDER_UNAVAILABLE")
        segments=dub_segments(load_segments(directory / "segments_translated.json"),directory,
                              configured[name],voice,cfg,media)
        save_segments(directory / "segments_dubbed.json",segments)
        paths={"dubbed":"segments_dubbed.json"}
        paths.update({f"tts_{s.id}":s.tts_file for s in segments})
        return commit(store,job_id,Phase.DUBBED,paths)
    return stage(store,job_id,Phase.DUBBED,operation)


def align_job(store,job_id,config,media):
    from .alignment import align_segments
    def operation(manifest):
        directory=store.directory(job_id)
        cleaned = directory / "clean/video.mp4"
        source_video = cleaned if "cleaned_video" in manifest.artifacts else Path(manifest.input_video)
        if source_video == cleaned and not cleaned.is_file():
            raise StateError("Committed cleaned video is missing")
        segments=align_segments(load_segments(directory/"segments_dubbed.json"),source_video,
                                directory,media,manifest.config_snapshot.alignment, job_id=job_id)
        save_segments(directory/"segments_aligned.json",segments)
        return commit(store,job_id,Phase.ALIGNED,{"aligned":"segments_aligned.json",
            "alignment_plan":"alignment_plan.json","alignment_result":"alignment_result.json",
            "aligned_video":"aligned/video.mp4","target_audio":"target.wav"})
    return stage(store,job_id,Phase.ALIGNED,operation)


def render_job(store,job_id,config,media):
    from .render import render_video, verify_output
    def render_operation(manifest):
        directory=store.directory(job_id)
        output=render_video(manifest,load_segments(directory/"segments_aligned.json"),directory,media)
        return commit(store,job_id,Phase.RENDERED,{"srt":"target.srt","ass":"target.ass"},
                      output_file=str(output),output_fingerprint=fingerprint(output))
    stage(store,job_id,Phase.RENDERED,render_operation)
    def verify_operation(manifest):
        directory=store.directory(job_id)
        expected=media.probe(directory/"aligned/video.mp4")["duration_ms"]
        output=Path(manifest.output_file)
        verify_output(output,media,expected)
        if fingerprint(Path(manifest.input_video))!=manifest.input_fingerprint:
            raise StateError("Source file changed")
        preview=directory/"preview.png"
        segments=load_segments(directory/"segments_aligned.json")
        midpoint=(segments[0].final_start_ms+segments[0].final_end_ms)/2000
        media.run(media.config.ffmpeg,["-v","error","-nostdin","-y","-ss",str(midpoint),
                                      "-i",str(output),"-frames:v","1",str(preview)])
        return commit(store,job_id,Phase.DONE,{"preview":"preview.png"})
    return stage(store,job_id,Phase.DONE,verify_operation)


def next_action(manifest):
    phase=manifest.last_successful_phase
    if phase==Phase.CREATED:
        return "CONFIRM_LANGUAGES"
    if phase==Phase.WAITING_VOICE and not manifest.voice_id:
        return "SELECT_VOICE"
    return {Phase.LANG_CONFIRMED:"transcribe",Phase.TRANSCRIBED:"clean",
        Phase.CLEANED:"AGENT_POLISH_THEN_VALIDATE",Phase.POLISHED:"AGENT_TRANSLATE_THEN_VALIDATE",
        Phase.TRANSLATED:"voices",Phase.WAITING_VOICE:"dub",
        Phase.DUBBED:"align",Phase.ALIGNED:"render",Phase.RENDERED:"render",Phase.DONE:"DONE"}[phase]
