# video-translate-agent

面向 Codex / Claude Code 的本地视频翻译与画面文字清理 Skill。当前版本 2.3.4（GPU STTN / CPU OpenCV 自动选择 + 服务端对齐）；真实外部服务验证状态见 [验收记录](docs/VALIDATION.md)。

## 功能与边界

视频 → Faster Whisper → RapidOCR 稀疏检测 → 服务端清理决策/字幕布局（仅上传元数据） → 本地 GPU STTN / CPU OpenCV 自动选择 → 当前 Agent 保守润色与本地翻译 → Python 结构校验 → ElevenLabs 音色选择（用户明确指定时可用 MiniMax）→ 逐段 TTS → LZStudio CLI 调用服务端对齐策略 → 本地音画执行 → SRT / ASS → 硬字幕 MP4。译文不发送到 Microsoft 或其他外部翻译服务。

源语言默认使用 `auto` 自动识别，不要求用户确认；用户已明确指定时尊重该选择，只有自动检测失败或无法得到可用语言结果时才询问源语言。目标语言必须明确；若未提供，只询问目标语言。翻译后从真实音色列表选择 Provider 与 Voice。ASR 润色不调用额外 LLM API，不暂停审稿。Python 负责全部数学时间轴和媒体处理。

ElevenLabs 是默认 TTS Provider。首次使用会自动打开本机安全浏览器向导，无需输入终端命令；MiniMax 仅在用户明确指定时使用。

成片完全移除原讲话、BGM、环境声和音效，只包含清理后的画面、目标 TTS 音轨和目标 ASS 硬字幕。不做人声分离、Demucs、混音、ducking、背景音恢复或额外远程 AI 修复。

### 服务端策略边界


Skill 通过 LZStudio CLI 提交对齐任务并轮询同一个任务 ID。n8n Webhook 保留附件中的 HeaderAuth；对齐决策仍无本地 fallback，服务端不可用时明确失败。

字幕/水印的 mask 生成与逐帧修复在本地执行；字幕布局和音画对齐使用 `VideoTranslatePolicyV1`，ASS 排版、TTS 变速和视频拉长在本地执行。

## 安装

灵智工坊 Key 可本机持久保存，但启动、创建任务和转写阶段不检查也不提醒。只有转写完成、首次进入字幕清理前需要策略服务时，才检查 Key；缺失时引导用户前往 [灵智工坊官网](https://www.lingzhiai.com.cn/) 获取。用户主动在聊天里提供 `LINGZHI_API_KEY` 或 `LZSTUDIO_API_KEY` 后，Agent 通过 `credential-set --provider lingzhi --stdin --json` 的标准输入保存；`lzstudio` 是别名。两者共用一份授权，读取优先级为非空环境变量 `LINGZHI_API_KEY` → `LZSTUDIO_API_KEY` → 已保存凭据。macOS 凭据目录权限 700、文件权限 600，Windows 使用系统凭据库；不写任务文件，不随发布包迁移。后续对话、进程重启和 Skill 更新自动复用，直到明确删除或替换。`credential-status --json` 仅显示名称，`credential-delete --provider lingzhi --json` 可删除。聊天提交为可选入口，原消息可能留在历史中，意外披露应删除原消息并轮换 Key。保存不代表远端授权有效。

本 Skill 支持 LZStudio CLI 0.0.5：macOS ARM64 和 Windows x64。解析顺序为 `LZSTUDIO_CLI` 显式覆盖、已安装的平台 CLI、PATH；若均不存在，支持的平台会从 GitHub v2.3.4 Release 下载对应资产并校验固定 SHA-256。macOS Intel 未附对应二进制，应通过 `LZSTUDIO_CLI` 指定兼容版本。CLI 不含用户授权，授权可来自环境变量或已保存的本机凭据。

Python 3.10+；依赖 `rapidocr`、`onnxruntime`、OpenCV、NumPy 与 Faster Whisper。另需可用的 LZStudio CLI；可用 `LZSTUDIO_CLI` 指定 CLI 绝对路径。灵智工坊环境变量或本机持久凭据只在转写完成、首次进入 `clean` 前需要，不是安装或启动前提。系统安装 FFmpeg / ffprobe，需含 subtitles/libass、atempo、libx264、AAC。Windows CPU 默认可运行，不要求 NVIDIA GPU。Windows 推荐在本地 NTFS 工作目录运行。Rubber Band 可选，缺失时使用 atempo。

```powershell
# 在解压后的 Skill 根目录执行
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

macOS 在 Skill 根目录执行：

```bash
sh ./install.sh
```

安装脚本检查 Python 3.10+、ffmpeg/ffprobe，在根目录创建 `.venv`，安装 `.[test]` 并执行 preflight。需要网络下载安装 Python 依赖；不打包 Python、FFmpeg 或模型。Python 3.12 是本次验收版本。首次 ASR 另需下载模型，支持 CPU；GPU 环境不是安装前提。

未配置 ElevenLabs Key 时，安装脚本自动打开本机安全向导和 ElevenLabs 官方 API Keys 页。用户创建受限 Key、设置可接受的额度上限，再将 Key 粘贴到本地密码框即可。取消向导不会导致依赖安装失败；下次翻译会重新打开。

Faster Whisper 默认 small、device=auto、compute_type=auto。首次运行会下载模型；已有缓存时复用。GPU 依赖以已安装 CTranslate2/CUDA 为准，可在 config/default.yaml 指定 cpu/int8。模型加载失败返回真实失败，不伪造字幕。

浏览器向导运行于随机的 `127.0.0.1` 地址，不加载第三方脚本。Key 在内存中完成免 TTS 消费的权限验证。macOS 使用仅当前用户可读的本地凭据文件（目录权限 `700`、文件权限 `600`），避免弹出“登录钥匙串密码”；Windows 使用 Credential Manager。Key 不写入 manifest、任务目录、日志、命令参数或浏览器存储。`credential-status --json` 只显示 Provider，`credential-delete --provider <provider>` 可删除。环境变量仍作为临时高优先级覆盖，不自动读取 `.env`。只在向导无法使用时，才降级到 `credential-set --provider elevenlabs` 的隐藏输入。

如果用户主动选择在聊天中发送 Key，Agent 可以启动 `credential-set --provider elevenlabs --stdin --json`，再通过该进程的标准输入保存。此入口不接受命令行 Key 参数，只读取一行且最多 4096 字符，CLI 输出不会回显 Key。聊天消息本身可能保留在聊天历史中，因此浏览器向导仍是默认方式；Agent 不应主动索要聊天 Key，也不得复述已收到的 Key。

## 使用 Skill

直接让 Agent：**按 D:/job/nt/SKILL.md 处理指定视频**。该方式使用本目录的真实脚本和配置，无需先复制 Skill。

## 迁移到另一台 Windows 电脑

先校验 ZIP 的 SHA-256，解压到可写的本地 NTFS 目录，保留 `video-translate-agent` 整个目录。安装 Python 3.10+ 和带 libass/subtitles、libx264、AAC、atempo 的 FFmpeg，并将 python、ffmpeg、ffprobe 加入 PATH。运行 `install.ps1` 后按自动打开的浏览器向导完成 ElevenLabs 连接；凭据保存到 Windows Credential Manager，无需重启 Agent，也不需以后重复提供。

### 方式 A：直接使用解压目录

例如解压到 `D:/xxx/video-translate-agent`，告诉 Agent：

```text
按 D:/xxx/video-translate-agent/SKILL.md 处理这个视频
```

Agent 应使用该目录 `.venv/Scripts/python.exe`。下方 CLI 示例中的 `python` 均替换为这个解释器，项目路径替换为实际解压目录。

### 方式 B：安装到 Agent skills 目录

本机已确认 Codex 用户 Skill 目录为 `C:/Users/Administrator/.codex/skills`，即本机 `%USERPROFILE%/.codex/skills`。在另一台电脑先确认其 Codex 用户 skills 位置，再将完整目录放到该位置的 `video-translate-agent` 子目录，随后在最终位置运行 `install.ps1`。不要复制旧电脑的 `.venv`，也不要仅复制 `SKILL.md`。

必须一起保留 `scripts/`、`video_translate/`、`config/`、`docs/`、`SKILL.md`、`pyproject.toml`、安装脚本及许可证文件；建议原样保留整个发布包。Claude Code 的 Skill 安装位置未在本机确认，本文不提供猜测路径，可先采用方式 A。

压缩包不含 jobs、output、凭据、媒体、日志、模型缓存或虚拟环境。安装及首次运行仍需要联网，外部 API 的权限和额度由使用者自行提供；preflight 不代表远端授权验证。

## CLI 与输出

```powershell
python D:/job/nt/scripts/video_translate.py credential-setup --json
# 仅当用户主动在聊天中提供 Key 时，由 Agent 通过进程标准输入调用：
python D:/job/nt/scripts/video_translate.py credential-set --provider elevenlabs --stdin --json
python D:/job/nt/scripts/video_translate.py preflight --json
python D:/job/nt/scripts/video_translate.py create --input "D:/video/中文 视频.mp4" --cleanup-mode local --json
python D:/job/nt/scripts/video_translate.py transcribe --job <id> --source auto --target en --json
python D:/job/nt/scripts/video_translate.py clean --job <id> --json
# Agent 按 SKILL.md 编辑 segments_polished.json 后：
python D:/job/nt/scripts/video_translate.py validate-polish --job <id> --json
# Agent 在 segments_translation.json 中本地填写译文，只修改 text
python D:/job/nt/scripts/video_translate.py validate-translation --job <id> --json
python D:/job/nt/scripts/video_translate.py voices --job <id> --json
# 从实际 voices.json 中选择 Provider 和 ID：
python D:/job/nt/scripts/video_translate.py dub --job <id> --provider minimax --voice-id <voice_id> --json
python D:/job/nt/scripts/video_translate.py align --job <id> --json
python D:/job/nt/scripts/video_translate.py render --job <id> --json
python D:/job/nt/scripts/video_translate.py status --job <id> --json
```

所有相对路径以项目根为基准，不依赖 cwd；--runtime-root 可指定其他运行目录。job 公开配置在 create 时固定，凭据从当前环境或系统凭据库加载。最终 MP4 在 output/<source>_<target>_dubbed.mp4；同名冲突使用 job_id 子目录，绝不覆盖源视频。每个 job 保留 clean/video.mp4、检测/布局/性能报告（含逐 Track 锚点覆盖率、动态 mask 均值与修补方法计数）、mask 与三张清理预览，以及 target.srt、target.ass、target.wav、preview.png、阶段快照和恢复资料。清理预览仅供诊断，正常翻译流程不做人眼质量校验，也不因残字、描边、平滑带或其他修补瑕疵而暂停或重试；clean 技术成功后直接进入后续阶段。

恢复只需 status，再按 next_action 执行；已确认语言和声音不得重问。dub 恢复可省略 Provider/Voice。成功必须是 DONE 且完整性检查通过。

## 测试与说明

```powershell
python -m pytest -q
```

普通测试默认排除 live，不消费 API 额度；含真实 FFmpeg 媒体测试，必须有本机工具。网络、ASR 推理和 TTS 的 mock 测试不等于真实 API 验证。完整情况见 [VALIDATION](docs/VALIDATION.md)。

授权错误码：`ELEVENLABS_CREDENTIAL_REQUIRED` 表示需要启动浏览器向导；向导使用 `CREDENTIAL_SETUP_TIMEOUT`、`CREDENTIAL_SETUP_CANCELLED`、`CREDENTIAL_PERMISSION_INSUFFICIENT` 和 `BROWSER_OPEN_FAILED`。标准输入为空或超长时返回 `EMPTY_CREDENTIAL` 或 `CREDENTIAL_INPUT_TOO_LARGE`。

策略相关错误：`LZSTUDIO_CLI_UNAVAILABLE`、`LINGZHI_CREDENTIAL_REQUIRED`、`POLICY_TIMEOUT`、`REMOTE_POLICY_FAILED`、`POLICY_OUTPUT_INVALID` 表示服务端策略通道不可用或返回契约无效，不允许本地策略 fallback。

错误码：0 成功，2 配置/状态/参数不合法，1 技术执行失败。FAILED 保存失败阶段与上次成功状态，不吞异常或丢片段。RAPIDOCR_UNAVAILABLE、ONNXRUNTIME_UNAVAILABLE、OPENCV_UNAVAILABLE 表示清理依赖缺失；CLEANUP_TIMEOUT 表示清理超过配置时限且不会回退为未清理视频。NO_TTS_PROVIDER_CONFIGURED 表示两家 Key 都缺失；NO_AVAILABLE_TTS_PROVIDER 表示实际音色获取均失败；ASR_EXECUTION_FAILED 表示模型/推理错误；INPUT_CHANGED_OR_MISSING 表示输入与 job 不符。检查配置与依赖后重试相同阶段，勿篡改 manifest 跳过验证。


发布维护流程见 [RELEASE](docs/RELEASE.md)。

本地清理默认 cleanup.backend=auto：验证 NVIDIA CUDA 优先，其次 Apple MPS，无可用加速设备使用 OpenCV。GPU 主机首次安装会从 GitHub v2.3.4 Release 下载固定 STTN 权重并校验 SHA-256；安装器按硬件安装可选 PyTorch，CPU-only 环境不强制安装。STTN 异常会尝试修复依赖/权重，内存不足最多两次减半分段；仍失败则整段回退 OpenCV，并记录原因。每段 300 秒超时独立于 OpenCV 总运行限制。已完成分段按输入和输出指纹复用。可设置 backend=opencv 强制原方案。


策略更新：两个动作共用一个工作流入口；接口 schema_version 与 policy_version 分开。缓存文件为 cleanup_policy_result.json / alignment_policy_result.json；客户授权记录为 policy_consent.json。客户端不包含清理分类和主字幕带评分规则。详细恢复和一次授权流程以 SKILL.md 为准。


Local cleanup has no total wall-clock deadline. Legacy `cleanup.max_runtime_seconds` is accepted but ignored and excluded from new configuration snapshots. OpenCV reports frame progress to stderr every 10 seconds during frame processing; stdout remains JSON. Full-video cleanup encoding has no subprocess deadline; STTN per-chunk and external-request timeouts remain enabled. On legacy CLEANUP_TIMEOUT, retry clean for the same job without editing its manifest or discarding valid policy caches. OpenCV restarts frame processing rather than resuming a partial AVI. Cancellation and exceptions release video handles and remove the current temporary AVI/MP4.
