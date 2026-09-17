# Third-party notices

## pyVideoTrans

Source: https://github.com/jianchang512/pyvideotrans

Pinned commit: `ad0b8bd8ec8a3cdceaab4358fd7fdc1ea19b3800` (2026-09-08).

License: GNU GPL version 3; full text is included in [LICENSE](LICENSE). This local project is distributed under GPL-3.0. Upstream authors retain their rights in the referenced/adapted material. No complete pyVideoTrans runtime or GUI framework is included.

| Upstream source | Local target | Actual use and changes |
|---|---|---|
| videotrans/process/stt_faster.py | video_translate/asr.py | Reference for WhisperModel/VAD/word timestamps; new isolated wrapper with explicit config, stable errors and deterministic auto-device fallback |
| videotrans/process/_stt_utils.py | video_translate/resegment.py | Adapted punctuation/pause/short-segment grouping approach; no GUI logging, SrtItem or TEMP_ROOT; handles missing words and lone short phrases without dropping text |
| videotrans/translator/_microsoft.py | docs/PYVIDEOTRANS_REFERENCE.md | Historical protocol study for v1.0; the runtime adapter was removed in v1.2 and current translation stays within the Agent |
| videotrans/tts/_elevenlabs.py, videotrans/util/help_role.py | video_translate/tts/elevenlabs.py | Protocol/voice identity reference; current HTTP API with dynamic pagination, IDs instead of name-keyed cache |
| videotrans/tts/_minimaxi.py | video_translate/tts/minimax.py | t2a_v2 payload and hex-audio parsing reference; no default voice substitution; remote voice list validation |
| videotrans/voicejson/minimaxi.json, minimaxiio.json | config/minimax_voices.json | Extracted both host-specific language catalogs from the pinned commit; removed No entries and combined under host keys. Voice IDs and names remain upstream data |
| videotrans/task/_rate.py, docs/Synchronize.md | video_translate/alignment.py | Studied upstream alignment behavior; client retains atempo/media execution principles while production decision policy is server-side |
| videotrans/configure/base.py, videotrans/util/_ffmpeg_runner.py, _ffprobe.py, _ffmpeg_audio.py | video_translate/media.py | Reference for argument-array subprocess use and PCM formats; new explicit dependencies, no global state |
| videotrans/task/_stage_align.py, _stage_subtitle.py, _stage_assemble.py | video_translate/workflow.py, subtitles.py, render.py | Stage-boundary, final-timeline and basename/cwd burn reference; new single-language render with only target audio |

The reviewed mappings and source links are in [PYVIDEOTRANS_REFERENCE](docs/PYVIDEOTRANS_REFERENCE.md). Models, manifest storage, CLI, tests and Agent protocol were written for this project; pyVideoTrans is not an import dependency. Upstream source functions were studied and selected algorithms/protocols adapted, rather than copied wholesale.

## Runtime dependencies and documentation

- [faster-whisper](https://github.com/SYSTRAN/faster-whisper), MIT: runtime ASR dependency. Its CTranslate2/runtime dependencies retain their respective licenses; installed via Python packaging, not vendored here.
- Pydantic, PyYAML and requests: installed Python dependencies, not vendored.
- [FFmpeg](https://ffmpeg.org/ffmpeg-filters.html): external executable; no binary bundled. Actual FFmpeg build licensing depends on its enabled components.
- [Rubber Band](https://breakfastquay.com/rubberband/): optional external executable; no binary bundled.
- [ElevenLabs API documentation](https://elevenlabs.io/docs/api-reference/voices/search) and [MiniMax API documentation](https://platform.minimax.io/docs/api-reference/speech-t2a-http): current protocol verification. No audio/voice clone dataset included.
- [Subtitle Edit](https://github.com/SubtitleEdit/subtitleedit): product-scope comparison only, no code used.

Windows speech synthesis used to create a short local validation input is test evidence only; it is not a production TTS provider or part of Skill runtime.



## STTN (ECCV 2020)
Bundled model and spectral-normalization code adapted from https://github.com/researchmm/STTN. MIT license is retained in video_translate/vendor/sttn/LICENSE. The unused torchvision import was removed; inference uses CUDA or Apple MPS. The bundled pretrained checkpoint is the upstream Youtube-VOS model.
