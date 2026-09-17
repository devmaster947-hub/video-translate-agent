# 架构与数据协议

## 职责和目录

Agent 负责两次用户选择、当前模型保守润色、本地目标翻译和阶段编排；Python 负责媒体、ASR、译文结构校验、TTS、时间计算与渲染。没有 GUI、Web、外部翻译 Provider、后台 Worker、全局任务 queue 或额外 LLM API。

| 模块 | 职责 |
|---|---|
| scripts/video_translate.py、cli.py | 跨 cwd 入口、UTF-8 JSON、错误码、命令参数 |
| config.py、credentials.py、languages.py | 公共配置/凭据分离、平台凭据存储；集中 Provider 语言码映射 |
| models.py | Segment、Voice、Artifact、Manifest、AlignmentSlot/Plan |
| files.py、state.py | 原子 JSON、manifest、系统文件锁、指纹、阶段顺序 |
| workflow.py | 阶段事务、前置验证、阶段输出提交、失败与恢复 |
| media.py | ffprobe、FFmpeg 子进程、WAV 标准化、仅画面提取 |
| asr.py、resegment.py、polish.py、translation.py | Faster Whisper、定稿前断句、Agent 润色与本地译文投影/校验 |
| overlay_detection.py | 稀疏 RapidOCR、文字 Track 构建；调用服务端 cleanup_policy 分类及生成布局 |
| overlay_mask.py、cleanup.py | 本地模式：三帧空间锚点、逐帧动态字形 mask、自适应 ROI 修补；三方模式：保留 OCR/布局分析后调用独立字幕清除工作流 |
| policy_client.py | 通过 LZStudio CLI 提交/轮询远端策略任务；负责两种策略的契约封装、缓存与验证 |
| network.py | TTS Provider 有限 HTTPS 重试与稳定错误码 |
| tts/base.py、tts/minimax.py、tts/elevenlabs.py | list_voices / synthesize 统一接口 |
| dubbing.py | 每段 TTS、签名与 SHA-256 缓存、48kHz PCM WAV |
| alignment.py | 执行远端返回的对齐计划、音频变速、局部视频拉长、实测时间轴 |
| subtitles.py、render.py | 最终 SRT/ASS、hard burn、成品解码验证 |
| config/minimax_voices.json | 固定上游提交提取的国内/国际站点语言目录 |
| tests/ | 单元、mock adapter、真实媒体与恢复回归 |

SKILL.md 是 Agent 执行入口；README/PIPELINE 说明当前使用方式。jobs/output 是本地运行资料，不能加入发布包。没有为未使用功能创建占位模块。

## Segment 与文件

Segment 统一字段：id、start_ms、end_ms、raw_text、polished_text、translated_text、tts_file、tts_duration_ms、final_start_ms、final_end_ms。整数毫秒严格校验，原段 start/end 在定稿后不变；final_* 另存。tts_file 是 job 相对路径，拒绝盘符、UNC、穿越和逃出 job 的链接。

segments_raw.json 是润色投影：id/start_ms/end_ms/raw_text/text。resegment 在此文件定稿前完成；Agent 只编辑 segments_polished.json 中的 text，validator 比较段数、顺序、ID、类型、时间、raw_text 和字段集合，成功产生 segments_polished_validated.json 与本地翻译投影 segments_translation.json。该翻译投影冻结 id/start_ms/end_ms/raw_text/polished_text，Agent 只写 text；Python 校验后产生 segments_translated.json。配音、对齐分别写 segments_dubbed.json、segments_aligned.json，均使用同一 Segment 模型；独立阶段文件避免覆盖已验证上游快照。

Voice 唯一身份为 provider + voice_id，名称仅展示；voices.json 保存稳定编号和实际可用列表。MiniMax 使用按 host/目标语言配置目录与远端 system_voice ID 的交集；ElevenLabs 动态分页读取。没有默认音色、克隆或静默换人。

## 配置与 Provider

公共配置由 YAML 加环境 model/host 解析后保存到 job；Key 优先从当前进程环境读取。缺失时，macOS 从当前用户的 `~/Library/Application Support/video-translate-agent/credentials.json` 读取（目录 `700`、文件 `600`），避免 Keychain 密码弹窗；Windows 从 Credential Manager 读取。浏览器向导和聊天保存入口都不接受命令行密钥参数；聊天入口只从标准输入读取一行、限制为 4096 字符，再调用同一存储层。Pydantic SecretStr 排除序列化与 repr，Key 不进入 job、YAML 或日志。已创建 job 使用保存的公开配置，不受 cwd 或后来修改 YAML 影响。现有运行中需改变模型/host/语言/voice 时创建新 job，避免错误复用付费产物。

preflight 至少需要一家 Key，两家都缺失失败；只证明配置存在，不证明远端可用。voices 只列实际可用 Provider，两家都可用时同时列出；一家真实失败而另一家可用时保留失败码并允许后者继续。

目标翻译由当前 Agent 直接完成，不调用 Microsoft、其他翻译 Provider、外部 LLM API、其他 Agent、CLI 或文本生成服务。Python 只校验译文编辑文件的字段、段数、顺序、冻结值和非空文本。languages.py 集中处理 20 种目标语与 locale 别名；未知语言拒绝。Whisper auto 检测保留实际语言。

TTS 逐段输出 tts/0001.wav 等，PCM s16le、48000Hz、双声道；真实 duration 由媒体探测确认。TTS 速度为 1，最终变速交给 alignment。缓存签名包含文本、provider、voice_id、model、host、语言、格式和版本，并验证文件 SHA-256。付费 synthesize 自动请求尝试为一次，避免对结果不明的超时无意重复计费；用户修复后同 job 可继续。

## 服务端策略边界

清理决策与音画对齐均由 VideoTranslatePolicyV1 决定，分别使用 cleanup_policy 和 alignment_policy。policy_client.py 只负责提交、轮询、缓存和契约验证，不在本地重算策略。

清理阶段的 OCR 与轨迹提取在本地执行；OCR文字、位置、时间及原口播分段时间发送给服务端，由服务端决定文字类别、清理资格和字幕布局。local 模式执行动态 mask，自动选择可用 CUDA/MPS STTN，否则 OpenCV。

对齐阶段服务端仅接收视频时长以及每段的 ID、起点和实测 TTS 时长；返回 `AlignmentPlan`，包括每段目标时长、音频速度和视频 stretch 因子。`alignment.py` 只执行并实测，不重算策略。

n8n 工作流没有 LLM 节点，策略调用的 LLM Token 成本为 0。Webhook保留附件HeaderAuth，客户端通过LZStudio CLI / 上层任务服务调用。

## 状态、锁与恢复

CREATED → LANG_CONFIRMED → TRANSCRIBED → CLEANED → POLISHED → TRANSLATED → WAITING_VOICE → DUBBED → ALIGNED → RENDERED → DONE。POLISHED 到 TRANSLATED 由 Agent 本地填写译文并运行结构校验完成。manifest 包含失败阶段、last_successful_phase、输入 SHA-256、公开配置快照、产物路径/大小/SHA-256、选择和成品指纹。

workflow 持有整个命令的 OS 文件锁；嵌套 state 写入复用当前线程持有的锁。JSON 同目录临时写、flush/fsync、原子替换，失败不破坏已成功 manifest。FAILED 保留成功状态，阶段命令幂等重入前校验已登记文件。未注册的中间结果按对应阶段的验证/缓存规则复用或重建。status 只读检查，不篡改状态。

语言在识别前持久化；声音在第一条付费合成前持久化，恢复不重问。DONE 的结果可直接读取，重跑已完成阶段不再次付费。结构校验与产物验证由业务阶段完成，不能仅凭状态枚举或文件存在宣称成功。

## 成片与 Windows

全部原声音删除；最终仅清理画面 + 目标 TTS + 目标 ASS。无 Demucs、Vocal/Instrument、BGM 保留、ducking、原声混音、环境音恢复，也不引入 ProPainter、LaMa 或 SAM。OCR 低频均匀采样并在本地形成 Track；文字分类、清理资格和目标字幕位置由服务端清理策略决定。本地模式只对获准清理的 Track 构造逐帧动态 mask 并执行 CUDA/MPS STTN 或 OpenCV 修复。alignment 在 CLEANED 后强制读取 clean/video.mp4，并只执行服务端返回的对齐计划。

ASS 只使用 final_* 时间轴。字幕位置来自客户端 `subtitle_layout.json`，无有效布局时由渲染层使用安全默认位置；布局由服务端清理策略生成。字幕样式、换行、背景框和最终编码仍属于客户端执行层。

FFmpeg 使用参数数组、shell=False、绝对 cwd，Windows 子进程隐藏窗口。ASS 烧录使用 render/target.ass 固定 basename，不将盘符放入滤镜；concat 也使用受控 basename。所有输入和输出路径通过独立参数传递。测试覆盖 Windows 中文、空格和单引号目录。

ASS 只使用 final_* 时间轴与服务端决定的字幕布局。客户端仅负责文字换行、背景绘制、样式和烧录，渲染层不重新推断主字幕区域。展示文本会转义可能被 ASS 解释为控制语法的字符；原始译文和 SRT 不变。成片编码 H.264/yuv420p + AAC，只有一个视频和一个目标音轨；检查 stream、codec、duration、完整解码、源文件指纹和 preview。输出通过同文件系统硬链接发布到 output，避免覆盖已有文件。

## 来源和验收

[上游固定提交拆解](PYVIDEOTRANS_REFERENCE.md)、[实际改编记录](../THIRD_PARTY_NOTICES.md)。本项目按 GPL-3.0 提供本地使用源码。测试与 live 状态见 [VALIDATION](VALIDATION.md)。
