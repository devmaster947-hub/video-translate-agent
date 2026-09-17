---
name: video-translate-agent
slug: video-translate-agent
version: 2.3.3
displayName: 视频翻译与配音助手
summary: 本地识别口播、清理原字幕，完成翻译、配音、音画对齐与硬字幕输出。
tags:
  - video
  - translation
  - dubbing
  - subtitles
  - ffmpeg
description: Translate and visually clean a local video with Faster Whisper, sparse RapidOCR overlay detection, server-owned cleanup decisions, local GPU STTN / OpenCV caption removal, Agent-local translation, dubbing, alignment, and positioned ASS subtitles. Use when a user wants a translated dubbed MP4 with original text subtitles or watermarks removed. No GUI except the local credential setup wizard; no external translation provider, voice cloning, source-audio preservation, cloud repair model, or extra LLM API.
---

# Local video translation

This distribution uses LZStudio CLI 0.0.5 on macOS ARM64 and Windows x64. The client uses an explicit `LZSTUDIO_CLI` override first, then a platform copy, then PATH; if none exists on a supported platform, it downloads the matching v2.3.3 GitHub Release asset and verifies its pinned SHA-256 before use. On macOS Intel, set `LZSTUDIO_CLI` to a compatible executable. Downloading the CLI does not configure authentication or authorize video uploads.

Use the absolute path to `scripts/video_translate.py` inside this skill directory. Run all commands with the same Python environment and the same `--runtime-root` if overridden. Paths resolve relative to the skill root, not the shell cwd. Read [the CLI protocol](docs/PIPELINE.md) for error and recovery details; inspect [architecture](docs/ARCHITECTURE.md) only when debugging internals.

Use `<skill-root>/.venv/Scripts/python.exe` on Windows or `<skill-root>/.venv/bin/python` on macOS, created by the matching installer, for every command below. If it is missing, follow the installation section in [README.md](README.md). Keep the entire skill folder together; do not install SKILL.md alone.

## Normal checkpoints

1. Use `auto` for the source spoken language by default and do not ask the user to confirm it. Honor an explicitly supplied source language. Ask for the source language only when automatic detection fails or does not produce a usable language result. The target language must be explicit; ask only for the target when it is missing.
2. Use **ElevenLabs as the default TTS Provider** unless the user explicitly requests MiniMax. After translation and the `voices` command, show the actual available voices for the selected Provider with the persisted display index and voice name, then ask the user to choose one index; this fixes **Provider and voice_id together**. Do not invent a voice, choose a voice without confirmation, silently change Providers, or require both API keys. When ElevenLabs is the default, do not present MiniMax voices merely because MiniMax is also configured.

No ASR review, translation approval, cleanup-quality review, alignment quality threshold, subtitle or render checkpoint. Cleanup supports only `local`: local media execution with remote cleanup decisions. Video and audio are never uploaded for cleanup. Do not ask about Both speed factors. A technical failure is an error recovery condition, not a normal checkpoint.

## New job

Confirm the input exists, then run. Before any external request, follow the one-time consent section below; in particular, obtain cleanup/alignment metadata consent before `clean`:

```text
python <skill-root>/scripts/video_translate.py preflight --json
python <skill-root>/scripts/video_translate.py create --input <absolute-video-path> --cleanup-mode local --json
```


`local` keeps video processing on the client with server-owned cleanup decisions.

Preflight requires the selected Provider, which defaults to ElevenLabs, plus media tools, Faster Whisper, RapidOCR, ONNX Runtime, OpenCV, and the LZStudio CLI policy channel. The policy channel uses `VideoTranslatePolicyV1` by default, configurable only through `VIDEO_TRANSLATE_POLICY_WORKFLOW_ID`; authentication is handled by LZStudio CLI. The Lingzhi Key resolves from non-empty `LINGZHI_API_KEY`, then non-empty `LZSTUDIO_API_KEY`, then the persisted `lingzhi` credential. Pass `--provider minimax` only when the user explicitly requested MiniMax. Preflight reports dependency/credential presence; it does not certify remote access or voice compatibility. Resolve credentials in this order: the current process environment, then the operating-system credential vault. Never print or put API keys in task files, command arguments, logs, manifests, or responses. Only the private credential backend may persist a Key. `.env` is not automatically loaded.

### Persistent Lingzhi / LZStudio credentials

Users may explicitly provide `LINGZHI_API_KEY` or `LZSTUDIO_API_KEY` in chat for the requested workflow. This authorizes the Agent to persist that Key for Lingzhi. Both names share one canonical `lingzhi` credential; `lzstudio` is an alias. If the service or intended use is ambiguous, clarify without repeating the Key. Do not store credentials from unrelated attachments or unrelated secrets.

Start this command as an interactive process, send only the raw Key plus one newline to its standard input, and wait for the secret-free JSON result:

```text
python <skill-root>/scripts/video_translate.py credential-set --provider lingzhi --stdin --json
```

Never repeat, quote, summarize or place the Key in commentary, responses, displayed commands, environment assignments, shell piping/echo, logs or temporary task files. The bounded stdin reader accepts one non-empty line up to 4096 characters. Use the existing private macOS local-user credential file (directory mode `700`, file mode `600`) or Windows Credential Manager, never macOS Keychain. Persistence survives future conversations, process restarts and Skill upgrades on this computer until explicitly deleted or replaced; it does not synchronize across computers.

After saving, run `credential-status --json` and automatically rerun preflight, then resume the existing job if present. Report only that Lingzhi was saved; storage success does not verify remote authorization. Automatically reuse it for subsequent jobs without asking again. When absent, offer hidden local input (`credential-set --provider lingzhi`) or explain that the user may explicitly choose chat input; chat submission must not be mandatory. Warn that a chat-sent Key may remain in conversation history; recommend deleting the original message and rotating the Key if disclosure was unintended. Delete only on explicit request with `credential-delete --provider lingzhi --json`.

When default preflight returns `ELEVENLABS_CREDENTIAL_REQUIRED`, immediately run the following command and tell the user that the secure setup page opened. Do not ask them to type a terminal command. The browser wizard is the default because a Key pasted into chat becomes part of chat history:

```text
python <skill-root>/scripts/video_translate.py credential-setup --json
```

The command opens a temporary Chinese setup page on `127.0.0.1` and the official ElevenLabs API Keys page. The user creates a restricted key, pastes it only into the local password field, and submits it. The wizard verifies `models_read` and `voices_read` without TTS spend, then stores the key in a macOS local-user credential file with directory mode `700` and file mode `600`, or in Windows Credential Manager. It exits on success, cancellation, or ten-minute timeout. It never writes the key into task files, logs, command arguments, browser storage, or responses, and it must not use macOS Keychain because that can trigger an unfamiliar login-keychain password prompt.

If automatic browser opening fails, use the safe `setup_url` printed with `BROWSER_OPEN_FAILED` to open the local page for the user. If the local wizard cannot be used, fall back to the hidden terminal prompt with `credential-set --provider elevenlabs`; this is the only normal use of the legacy command. After successful setup, rerun preflight automatically.

If the user explicitly chooses to send an API Key in chat, or has already sent one, treat that message as authorization to save that Key for the requested Provider. Never repeat, quote, summarize, log, or place the Key in commentary, responses, a command argument, an environment variable, or a temporary file. Start the following command in an interactive process, send only the raw Key plus one newline to its standard input, and wait for the secret-free JSON result:

```text
python <skill-root>/scripts/video_translate.py credential-set --provider elevenlabs --stdin --json
```

Do not interpolate the Key into the displayed command or use shell piping/echo. The stdin reader accepts one non-empty line up to 4096 characters and stores it through the same platform credential backend. After success, run `credential-status --json` and rerun preflight; tell the user only that the Provider was saved. Also warn that the original chat message may remain in chat history and recommend deleting that message or rotating the Key if exposure was unintended. Never request the Key in chat as the default setup method.

When the user explicitly requests MiniMax, use `preflight --provider minimax` and the legacy hidden `credential-set --provider minimax` flow. The same `--stdin` handling is allowed only if the user explicitly chooses or has already sent a MiniMax Key. Never start ElevenLabs setup for an explicit MiniMax job.

Use `credential-status --json` to verify only the Provider name. Once stored, automatically reuse it on later turns and jobs. `credential-delete --provider <provider>` removes it when explicitly requested. Environment variables remain a temporary higher-priority override.

After the target language is known, use an explicitly supplied source language or default to `auto`:

```text
python <skill-root>/scripts/video_translate.py transcribe --job <id> --source <source-or-auto> --target <target> --json
python <skill-root>/scripts/video_translate.py clean --job <id> --json
```

The clean command performs sparse OCR and text-track construction locally, then submits one `cleanup_policy` task to VideoTranslatePolicyV1 with OCR text, track positions/times, source segment times and video metadata. The server alone decides classification, cleanup eligibility and dominant subtitle layout. After dubbing, one separate `alignment_policy` task sends segment IDs/start times, video duration and measured TTS durations. Neither policy uploads video or audio. Do not combine the stages or wait for TTS before cleanup. Saved fingerprint-checked policy results are reused; when a task ID was saved, recovery polls that same task. Do not substitute local policy rules.

- `local`: local OCR and remote cleanup decisions guide local mask construction. With cleanup.backend=auto (default), use STTN on verified CUDA first, then MPS / Apple GPU; otherwise use OpenCV. STTN runs locally with a pinned SHA-256-verified weight downloaded from the v2.3.3 GitHub release on GPU hosts, 2.5-second chunks, 432×240 inference and a 300-second timeout per chunk. Use actual video track masks and positions, never sample-specific coordinates. Cache completed chunks with input/output fingerprints. Repair missing GPU dependencies/weights locally; on out-of-memory halve chunk length, retry at most twice. If STTN remains unavailable or fails, rerun the whole cleanup with OpenCV using the same OCR analysis and explicitly report the fallback reason. cleanup.backend=opencv forces OpenCV; sttn requests GPU STTN with the same documented fallback. The installer prepares optional GPU dependencies; preflight reports availability without blocking CPU-only machines. The selected backend repairs frames, and writes `clean/video.mp4`, `overlay_analysis.json`, `subtitle_layout.json`, masks, reports, and diagnostic previews.

If either cleanup/alignment policy is unavailable or returns an invalid contract, treat it as a technical failure rather than silently using local heuristics.

`preview/cleanup_before.png`, `preview/cleanup_mask.png`, and `preview/cleanup_after.png` are diagnostic artifacts only. Do not inspect them, create contact sheets, request cleanup approval, or block/retry the workflow because of visible source-text residue, outlines, smoothing bands, or other cleanup imperfections. Once `clean` returns `ok=true` with phase `CLEANED`, continue directly to polishing and translation. Technical cleanup failures remain error recovery conditions: do not substitute the input video or claim success after `CLEANUP_TIMEOUT`.

After CLEANED, read `jobs/<id>/segments_raw.json`. Using **your own current model capability**, write `segments_polished.json` in the same directory, modifying only each object's `text`. Preserve exactly `id`, `start_ms`, `end_ms`, `raw_text`, field set, object count and order. Correct obvious recognition/homophone mistakes and punctuation conservatively; retain uncertain original wording. Do not add information, summarize, rewrite extensively, split or merge segments. Do not invoke another LLM API, another agent for translation, or Python text-generation service. For long files, process bounded contiguous groups, then reconstruct the entire original order before validation; never omit groups.

```text
python <skill-root>/scripts/video_translate.py validate-polish --job <id> --json
```

The polish command creates `jobs/<id>/segments_translation.json`. Using your own current model capability, translate each `polished_text` into the manifest's target language by modifying only each object's `text`. Preserve exactly `id`, `start_ms`, `end_ms`, `raw_text`, `polished_text`, field set, object count and order. Preserve brand/product identity and exact model/shade codes; localize descriptive shade names according to the guidance below. Preserve meaning and sales tone, and do not add or omit claims. Do not call Microsoft or any other translation provider, external LLM API, agent, CLI, or text-generation service. For long files, translate bounded contiguous groups and reconstruct the complete original order before validation.

### Product and shade names in Japanese

- Distinguish brand names, model/shade codes (such as `V07`), official shade names, and descriptive color wording. Keep codes exact. Use an official Japanese name only when supported by supplied material or a verified official source; do not invent a localized official name or a brand pronunciation.
- Shared Chinese/Japanese kanji are not evidence that a phrase has been naturally translated. For descriptive shade names without an established Japanese name, choose idiomatic Japanese wording or a suitable Japanese rendering while retaining the original identity. For example, `V07 白桃清酒` is a shade reference, not a beverage claim: simply copying `V07、白桃清酒。` is insufficient without considering whether the name needs localization. An explanatory rendering such as `V07、白桃と日本酒をイメージしたカラー。` may fit a descriptive name; if the exact official name must be retained, use `V07「白桃清酒」` and do not present the explanation as its official name. Do not force every shade name into this example's wording or add product ingredients, benefits, or color details absent from the source.
- Before `validate-translation`, review Japanese name-bearing segments for naturalness, name/code consistency, and intended spoken reading, especially kanji names and alphanumeric codes. The current pipeline uses the same translated `text` for subtitles and TTS: choose wording suitable for both; use kana where a descriptive term's reading would otherwise be ambiguous, without changing exact codes or inventing readings for uncertain proper names. Do not add unsupported pronunciation fields or promise a particular TTS reading without evidence. This is an Agent translation check, not a new user approval checkpoint; structural validation alone does not establish translation quality.

```text
python <skill-root>/scripts/video_translate.py validate-translation --job <id> --json
python <skill-root>/scripts/video_translate.py voices --job <id> --json
```

Filter the returned list to the selected Provider before presenting the voice checkpoint. Default to ElevenLabs; do not fall back to MiniMax if ElevenLabs voice discovery fails. Report and recover the ElevenLabs error unless the user explicitly changes Providers.

When polish or translation validation fails, fix the editable file against its validated source without asking the user. Do not alter frozen upstream files to make a validator pass.

Before the first external request, obtain any missing authorization using this short Chinese sentence:

> 本任务需向灵智发送字幕文字、位置和时间，以及配音时长，用于字幕清理和音画对齐，不上传视频或音频。是否允许？

This covers both policy actions in the same job, including TTS duration measured later. Persist the granted scope as `policy_consent.json` in that job directory (`scope: cleanup_and_alignment_metadata`, `granted: true`). Record it only after explicit consent or an existing explicit authorization covering that scope; never infer approval from elapsed time. On recovery reuse granted consent, and do not ask once per action. Do not initiate `clean` without this consent. If the customer asks what is sent, explain that metadata also includes video duration and source segment timings. Separately disclose that translated text is sent to the selected TTS Provider; existing matching consent can be reused. This does not bypass platform tool approvals.


After checkpoint 2, map the selected index using that job's `voices.json`:

```text
python <skill-root>/scripts/video_translate.py dub --job <id> --provider <provider> --voice-id <id> --json
python <skill-root>/scripts/video_translate.py align --job <id> --json
python <skill-root>/scripts/video_translate.py render --job <id> --json
python <skill-root>/scripts/video_translate.py status --job <id> --json
```

Automatically proceed through these steps, fixing ordinary technical problems within existing authorization. Target ASS subtitles default to a 22.4px base font size at 720p (80% of the previous 28px default), with final pixel size rounded by the renderer. Background width, height, padding and corner radius follow the reduced rendered glyph size; do not retain the previous backdrop dimensions. Use one video-wide base font size, scaled only by video height relative to 720p; never vary font size or style according to cue length or the detected source-text box. Reflow long text at word boundaries to at most two balanced lines. Each subtitle event has exactly one tightly text-hugging rounded dark-gray background shared by the entire one- or two-line block, never one box per line or an OCR-box-width rectangle. Size the backdrop to the visible glyph block, with about 0.06em vertical safety padding and no extra nominal font-line leading. Cap the background at 88% of frame width and clamp it inside the frame. The background defaults to alpha 176 (about 69% transparent) so it does not strongly obscure the picture. Only report success for `phase=DONE`, `ok=true`, no integrity errors, and a target subtitle position accepted from the remote cleanup-policy result or the renderer's safe fallback. The render stage probes and decodes the final MP4, checks the source fingerprint and writes `preview.png`; do not add a manual preview-quality gate. Report the absolute `output_file` plus `target.srt`, `target.ass` and `target.wav` paths. Never overwrite the input video.

## Resume across turns

Always run `status --job <id> --json` first; read saved `policy_consent.json` and reuse matching consent; do not rely on conversation memory. Preserve the same job ID.

- `CREATED`: ask only if the target language is missing; otherwise run `transcribe` with an explicit source language when supplied, or `auto` by default.
- `LANG_CONFIRMED`: `transcribe` with the manifest's saved languages.
- `TRANSCRIBED`: run `clean` without a user checkpoint.
- `CLEANED`: read raw, finish your own polish edit, `validate-polish`; never repeat cleanup.
- `POLISHED`: translate `segments_translation.json` locally with your current model, then `validate-translation`.
- `TRANSLATED`: `voices`.
- `WAITING_VOICE`: checkpoint 2 only if `voice_id` is absent; otherwise `dub --job <id>` without asking again.
- `DUBBED`: `align`; the stage must use committed `clean/video.mp4`, never the original source.
- `ALIGNED` or `RENDERED`: `render`.
- `DONE`: verify status and return existing paths; do not redo paid work.
- `FAILED`: use `last_successful_phase`, `failed_stage` and the stable error code to repair the real problem, then retry that stage. Saved language and voice choices remain authoritative. Valid TTS and translation caches are reused. Input/committed-artifact fingerprint errors require diagnosing the changed file; never clear integrity errors to claim success.

## Media boundary

Final output is cleaned video picture + target-language TTS only + target ASS hard subtitles. All original speech, BGM, ambience and sound effects are discarded. Do not add Demucs, vocal/instrument separation, ducking, mixing, ambience recovery, ProPainter, LaMa, SAM, or any remote media repair service. Preserve `scene_text`; manual normalized regions are only for explicitly configured non-text logo watermarks. Final subtitle time always comes from `final_start_ms`/`final_end_ms`, while position comes from the server-decided `subtitle_layout.json` with documented layout fallbacks.

Python owns timing execution, while the remote alignment policy decides the per-slot target duration, audio speed, and video stretch factor. The client must execute the returned validated plan and must not recreate or substitute the decision rule locally. Audio/video failures must not be hidden by dropping segments, truncating speech, inserting fake missing TTS, or pretending an unmodified clip was successfully slowed.

## Service authorization output

Lingzhi is a service authorization channel. Do not query or calculate its account usage, or disclose its credits, balance, limits, deductions, or administrator reset mechanism in progress, consent, summaries, or error explanations. Do not display raw Lingzhi responses or run its account/billing commands. Preserve the external data-transfer disclosure above. For `SKILL_SERVICE_UNAVAILABLE`, stop further submissions and report exactly: `Skill授权异常，请联系管理员处理，微信：marlon1102`. Keep the job and completed artifacts; after the administrator restores service, resume through the existing fingerprint-validated recovery flow. Do not automatically retry this error or bypass remote authorization. Other technical errors retain their normal recovery behavior. This rule concerns Lingzhi only; customer-owned ElevenLabs/MiniMax errors remain accurate.

## Policy failure recovery

Both actions use the same configured workflow ID, schema version 1, and separate fingerprint-checked result caches: `cleanup_policy_result.json` and `alignment_policy_result.json`. Each stores the server policy version. Upgrading server rules must not invalidate a completed plan. No local decision fallback is allowed. `POLICY_SUBMISSION_UNCERTAIN` means submission may have succeeded before the task ID was saved: recover the real ID through administrator/task records before retrying, never delete the marker to blindly resubmit. A terminal failed saved task needs diagnosis and deliberate replacement after the real failure is fixed; timeouts continue polling its existing ID. Changed-input or cache-integrity errors require diagnosing the changed artifact, not clearing checks. Existing CLEANED/DONE jobs remain reusable and do not rerun cleanup.


Local cleanup has no total wall-clock deadline. Legacy `cleanup.max_runtime_seconds` is accepted but ignored and excluded from new configuration snapshots. OpenCV reports frame progress to stderr every 10 seconds during frame processing; stdout remains JSON. Full-video cleanup encoding has no subprocess deadline; STTN per-chunk and external-request timeouts remain enabled. On legacy CLEANUP_TIMEOUT, retry clean for the same job without editing its manifest or discarding valid policy caches. OpenCV restarts frame processing rather than resuming a partial AVI. Cancellation and exceptions release video handles and remove the current temporary AVI/MP4.
