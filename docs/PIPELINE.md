# CLI 与恢复协议

统一入口：`python <项目绝对路径>/scripts/video_translate.py <command> --json`。作业命令支持 --config、--runtime-root。相对路径基于项目根，stdout/stderr 为 UTF-8，不依赖 cwd。返回 0 成功、2 参数/配置/契约错误、1 技术执行失败；不把原始 HTTP 响应或秘密放进错误信息。

| 命令 | 输入与结果 |
|---|---|
| credential-setup | 默认打开 ElevenLabs 官方 Key 页和本机安全向导；验证后写入系统凭据库 |
| credential-set --provider <name> | 隐藏输入，一次性保存到平台凭据存储 |
| credential-set --provider <name> --stdin | 仅在用户主动通过聊天提供 Key 时，由 Agent 经进程标准输入保存 |
| credential-status | 只列出已配置的 Provider，不输出 Key |
| credential-delete --provider <name> | 删除指定 Provider 的已保存凭据 |
| preflight [--provider <name>] | 本机工具、滤镜、编码器、faster-whisper、LZStudio CLI 和所选 TTS Provider Key；默认 ElevenLabs。灵智凭据仅报告为延后就绪信息，不阻断启动 |
| create --input <video> [--cleanup-mode local] | 验证视频并冻结清除模式，创建 jobs/<id>/manifest.json，CREATED |
| transcribe --job <id> --source <auto/语言> --target <语言> | 保存 LANG_CONFIRMED，提取源音轨，Faster Whisper、resegment，写 segments_raw.json，TRANSCRIBED |
| clean --job <id> | 稀疏 OCR/Track 构建 → 服务端清理策略/字幕布局（上传OCR文字、位置和时间等元数据，不上传媒体） → local: 动态 mask → CUDA/MPS STTN（auto）或 OpenCV，写 clean/video.mp4 与布局/报告，CLEANED |
| validate-polish --job <id> | 校验 Agent 只改 text 的 segments_polished.json，写统一 Segment 快照，POLISHED |
| validate-translation --job <id> | 校验 Agent 本地填写的目标译文并提交 segments_translated.json，TRANSLATED |
| voices --job <id> | 获取实际 Provider/voice ID，保存 voices.json，WAITING_VOICE |
| dub --job <id> --provider <name> --voice-id <id> | 保存一次选择，逐段 TTS/规范化/缓存，DUBBED；恢复可省略已保存选择 |
| align --job <id> | 实测 TTS → LZStudio CLI 请求远端对齐计划 → 本地执行变速/拉长并生成 target.wav、aligned/video.mp4、final_*，ALIGNED |
| render --job <id> | SRT/ASS、hard burn、输出 MP4，RENDERED；验证可解码/指纹并截取预览后 DONE |
| status --job <id> | 只读返回 manifest、完整性问题、resume_from、next_action |

## Agent 操作

灵智工坊凭据也支持安全持久保存。启动、创建任务和转写阶段不得提醒或索取灵智 Key；转写完成、首次执行 `clean` 之前才检查 `policy_credential_available`。缺失时提示：`即将进行字幕清理，需要灵智工坊 API Key。请前往 https://www.lingzhiai.com.cn/ 获取。` 优先提供 `credential-set --provider lingzhi` 的本机隐藏输入，不默认要求把 Key 发到聊天。用户主动在聊天里提供 `LINGZHI_API_KEY` 或 `LZSTUDIO_API_KEY` 用于当前工作流时，Agent 启动 `credential-set --provider lingzhi --stdin --json`，通过标准输入发送原始 Key 和一个换行；`lzstudio` 是同义 Provider。不得把 Key 插入命令、任务文件、日志、回复或临时文件。保存到现有私有本机凭据后，只检查 Provider 名称并重跑 preflight；后续自动复用。环境变量优先于已存凭据。用户可明确请求 `credential-delete --provider lingzhi --json` 删除。聊天输入为用户主动选择，需提醒原消息可能留在聊天历史；意外披露应删除原消息并轮换 Key。

严格按 [SKILL.md](../SKILL.md)：源语言默认使用 `auto` 且不询问用户；用户明确指定时使用其选择，仅在自动检测失败或结果不可用时询问源语言。目标语言未提供时只询问目标语言。TTS Provider 默认使用 ElevenLabs，仅在用户明确指定时改用 MiniMax；不在 ElevenLabs 失败时静默回退。默认 preflight 返回 `ELEVENLABS_CREDENTIAL_REQUIRED` 时，Agent 立即运行 `credential-setup --json`，不要求用户输入命令。浏览器向导仍是默认入口；只有用户主动选择或已经在聊天中发来 Key 时，Agent 才可用交互进程的标准输入保存，并且不得复述 Key。成功后自动重跑 preflight。翻译后只展示已选 Provider 的真实音色列表，并进行一次 Voice 选择。除此之外不设置正常暂停点。Agent 使用当前模型完成润色和目标翻译，不调用 Microsoft、其他翻译 Provider、额外 LLM API、其他 Agent、CLI 或文本生成服务；Python 只验证文件结构并提交阶段产物。

`credential-setup` 仅绑定 `127.0.0.1`，使用随机 URL token、CSRF、请求大小限制、`no-store` 和严格 CSP。页面指导用户在 ElevenLabs 创建 `video-translate-agent` Key，开启 `text_to_speech`、`voices_read`、`models_read`，并设置额度上限与可选有效期。Key 仅在内存中验证；macOS 写入目录权限 `700`、文件权限 `600` 的当前用户凭据文件，不使用 Keychain；Windows 写入 Credential Manager。取消、失败或十分钟超时不保存。浏览器打开失败时使用 stderr 中的 `setup_url`；本地向导不可用时才降级到 `credential-set --provider elevenlabs`。

聊天 Key 入口使用 `credential-set --provider <name> --stdin --json`。Key 只能作为交互子进程的一行标准输入发送，长度上限 4096 字符；禁止命令参数、环境变量、shell 管道/echo、临时文件和日志。保存成功后只检查 Provider 状态。聊天历史本身不由 CLI 控制，Agent 必须提醒用户可删除原消息，意外暴露时应轮换 Key。

阶段命令成功后直接执行下一步。清理分类、资格和字幕布局通过 cleanup_policy 上传 OCR 元数据并在服务端决定。对齐通过 alignment_policy 上传片长、段ID/起点和实测配音时长。两个动作共用一次任务内授权，不上传视频或音频；每个动作保存任务ID和带指纹的结果缓存。对齐不做本地fallback。旧任务状态/文件格式不变，已完成清理、翻译和TTS按指纹验证后复用，升级后继续复用有效产物。清理预览仅作诊断，不增加质量检查点。三方模式仍单独调用字幕清除工作流并上传视频。

## 文件协议

- 冻结输入：segments_raw.json（id/start_ms/end_ms/raw_text/text）。
- 清理：写出 clean/video.mp4、overlay_analysis.json、subtitle_layout.json、cleanup_report.json，并保留 masks/ 与 preview/cleanup_*.png。
- Agent 润色：segments_polished.json，除 text 外逐字段、类型、顺序和数量必须一致。
- Agent 本地翻译：segments_translation.json，除 text 外逐字段、类型、顺序和数量必须与已验证润色源一致。
- 统一阶段快照：segments_polished_validated.json、segments_translated.json、segments_dubbed.json、segments_aligned.json。
- 音色：voices.json，包括 stable display_index、provider、voice_id、voice_name 与不可用 Provider 的稳定失败码。
- 逐段配音：tts/<四位id>.wav，PCM s16le/48000Hz/2ch，另有签名收据；不覆盖原 TTS 做变速。
- 对齐：alignment_plan.json、alignment_result.json、aligned/、target.wav。
- 输出：target.srt、target.ass、preview.png；MP4 位置见 manifest.output_file。

## 恢复

每次新 Turn 先 status，同一 job ID 继续。FAILED 使用 last_successful_phase 和 failed_stage；修复技术问题后重跑失败阶段。已确认语言不重问；声音已保存则 `dub --job <id>` 复用，不重新要求选择。完整性错误必须查明改动文件，不能删掉指纹绕过验证。

| last_successful_phase | 下一步 |
|---|---|
| CREATED | 目标语言已知时以显式源语言或默认 `auto` 执行 transcribe；仅目标语言缺失时询问 |
| LANG_CONFIRMED | 使用已保存语言 transcribe |
| TRANSCRIBED | 首先检查延后的灵智 Key；缺失时提供官网入口，配置后确认元数据授权，再执行 clean |
| CLEANED | Agent 自动润色并 validate-polish |
| POLISHED | Agent 本地翻译 segments_translation.json 后 validate-translation |
| TRANSLATED | voices |
| WAITING_VOICE | 没 voice 时唯一音色选择；已有 voice 时 dub |
| DUBBED | align |
| ALIGNED / RENDERED | render |
| DONE | status 验证并返回已有结果 |

没有独立 resume 子命令，恢复由 status + 阶段命令组成。阶段执行全程持有文件锁，进程结束自动释放；.lock 文件允许长期保留。已完成阶段会验证产物后返回，不重复 API 消费。输入/配置变更用新 job，避免缓存污染。

## 真实验证

`python -m pytest -q` 默认不执行 live；media 测试真实使用 FFmpeg，其他外部服务用 mock。真实全链路需要短视频、实际 Key、用户选择的声音，以及 Agent 亲自生成的 polished 文件，不能由测试程序伪造模型润色。

前置已获用户授权时，可按上述 CLI 走完整短视频 E2E；没有音色选择则到 WAITING_VOICE 把实际列表交给用户。成片必须通过 ffprobe 流/时长/编码检查、FFmpeg 完整解码和源文件 SHA-256 不变等技术完整性检查；不增加人工 preview 质量关卡。真实服务与测试详情见 [验收记录](VALIDATION.md)。

本地 cleanup.backend 默认 auto，设备选择 CUDA → MPS → OpenCV；报告包含 backend/device/fallback_reason。STTN 使用 Skill 内权重，每段 2.5 秒、300 秒超时，内存不足最多两次减半重试；失败后整段回退 OpenCV，复用本地 OCR 分析。分段缓存带输入/输出指纹，已 CLEANED/DONE 的任务不重新处理。preflight 的 STTN 可选能力不加入阻断性 errors。

灵智服务返回 `SKILL_SERVICE_UNAVAILABLE` 时停止提交，保留任务与已完成产物，提示：Skill授权异常，请联系管理员处理，微信：marlon1102。管理员恢复服务后按已有指纹恢复协议继续；网络超时和依赖错误仍按各自错误码恢复。


策略更新：两个动作共用一个工作流入口；接口 schema_version 与 policy_version 分开。缓存文件为 cleanup_policy_result.json / alignment_policy_result.json；客户授权记录为 policy_consent.json。客户端不包含清理分类和主字幕带评分规则。详细恢复和一次授权流程以 SKILL.md 为准。


Local cleanup has no total wall-clock deadline. Legacy `cleanup.max_runtime_seconds` is accepted but ignored and excluded from new configuration snapshots. OpenCV reports frame progress to stderr every 10 seconds during frame processing; stdout remains JSON. Full-video cleanup encoding has no subprocess deadline; STTN per-chunk and external-request timeouts remain enabled. On legacy CLEANUP_TIMEOUT, retry clean for the same job without editing its manifest or discarding valid policy caches. OpenCV restarts frame processing rather than resuming a partial AVI. Cancellation and exceptions release video handles and remove the current temporary AVI/MP4.
