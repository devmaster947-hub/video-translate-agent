# pyVideoTrans 参考实现拆解

## 1. 调研基线与结论

调研日期：2026-09-09。主要参考 [jianchang512/pyvideotrans](https://github.com/jianchang512/pyvideotrans)。本次实际读取的 main 提交为 **`ad0b8bd8ec8a3cdceaab4358fd7fdc1ea19b3800`**，提交时间 `2026-09-08T21:48:17+08:00`。以下上游事实均针对该提交，不把以后 main 的变化自动视为本项目规则。

已通过临时目录中的稀疏检出读取源码，无依赖安装、无上游程序执行。检出位于项目外，仅用于本次研究；交付物不依赖该临时副本。文档中的固定提交链接可重新定位证据。


## 2. 方案与复用成本评估

| 来源 | 本轮核查范围 | 用途与复用判断 | 成本与风险 |
|---|---|---|---|
| pyVideoTrans | 指定提交源码、LICENSE、同步文档、CLI 与阶段入口 | 主参考；优先提取 ASR 断句、对齐及媒体调用思路 | 仓库含 GPL-3.0 许可证；存在近期提交，但近期活动不等于安全审计。配置、GUI 信号、缓存和多渠道耦合较重，不能整模块照搬 |
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | 官方 README 与调用示例 | 直接使用其 Python 库，不自行实现 ASR 引擎；交叉核查 word timestamps、VAD 和设备配置 | 仓库标示 MIT；GPU 运行依赖 CTranslate2/CUDA 组合，需在实施阶段锁定并实测版本 |
| [Subtitle Edit](https://github.com/SubtitleEdit/subtitleedit) | 仓库入口层面的范围对比，未做源码级拆解 | 作为字幕编辑产品对照，不引入其应用架构 | 当前任务需要 Python CLI 与自动编排，移植桌面字幕编辑器超出范围；未将其代码列为复用候选 |
| [FFmpeg 官方滤镜文档](https://ffmpeg.org/ffmpeg-filters.html) | atempo、setpts、字幕滤镜参考入口 | 使用已有媒体工具；核对上游命令与目标平台行为 | 实际能力取决于安装构建，preflight 必须检测 libass / 编码器 / 滤镜；Rubber Band 作为可选工具检测 |

上游仓库的维护判断仅限本次提交记录和所读代码；本轮没有对整个依赖树或远端服务做安全审计。Microsoft Edge 接口的可用性、限流，以及两家 TTS 的当前账号权限，均需后续 live 验证。

## 3. 上游文件 → 能力 → 复用 → 目标模块

以下目标路径均相对于拟建的 `video_translate/` 包，都是设计而非已实现文件。

| 上游文件（固定提交） | 需要的能力 | 复用方式 | 目标模块 |
|---|---|---|---|
| [trans_create.py](https://github.com/jianchang512/pyvideotrans/blob/ad0b8bd8ec8a3cdceaab4358fd7fdc1ea19b3800/videotrans/task/trans_create.py) | 阶段组成、媒体路径、任务参数 | 参考职责；不继承 mixin 容器 | `cli.py`、`state.py`；Agent 编排写入 SKILL.md |
| [cli.py](https://github.com/jianchang512/pyvideotrans/blob/ad0b8bd8ec8a3cdceaab4358fd7fdc1ea19b3800/cli.py#L264-L272)、[task/job.py](https://github.com/jianchang512/pyvideotrans/blob/ad0b8bd8ec8a3cdceaab4358fd7fdc1ea19b3800/videotrans/task/job.py) | 确认实际调用顺序 | 参考，不复用线程与 queue | `cli.py` 和 SKILL.md |
| [process/stt_faster.py](https://github.com/jianchang512/pyvideotrans/blob/ad0b8bd8ec8a3cdceaab4358fd7fdc1ea19b3800/videotrans/process/stt_faster.py) | WhisperModel、VAD、字词时间戳、检测语言 | 改编最小调用逻辑，去除配置和临时日志耦合 | `asr.py` |
| [process/_stt_utils.py](https://github.com/jianchang512/pyvideotrans/blob/ad0b8bd8ec8a3cdceaab4358fd7fdc1ea19b3800/videotrans/process/_stt_utils.py) | 按标点、停顿、时长 resegment；秒转毫秒 | `_resegment` 为改编候选；不移植二次识别 `_resegment2` | `resegment.py` |
| [translator/_microsoft.py](https://github.com/jianchang512/pyvideotrans/blob/ad0b8bd8ec8a3cdceaab4358fd7fdc1ea19b3800/videotrans/translator/_microsoft.py) | Edge endpoint、字符串数组请求、translations 解析 | 改编协议；保留 TLS 验证，返回数组 | `translation.py` |
| [translator/_base.py](https://github.com/jianchang512/pyvideotrans/blob/ad0b8bd8ec8a3cdceaab4358fd7fdc1ea19b3800/videotrans/translator/_base.py) | 批处理、重试、缓存边界 | 仅参考；不移植按换行切回字幕及补空行 | `translation.py`、`state.py` |
| [tts/_elevenlabs.py](https://github.com/jianchang512/pyvideotrans/blob/ad0b8bd8ec8a3cdceaab4358fd7fdc1ea19b3800/videotrans/tts/_elevenlabs.py) | 逐条调用、voice_id、模型、流式字节落盘 | 最小适配；无全局 params | `tts/elevenlabs.py` |
| [tts/_minimaxi.py](https://github.com/jianchang512/pyvideotrans/blob/ad0b8bd8ec8a3cdceaab4358fd7fdc1ea19b3800/videotrans/tts/_minimaxi.py) | t2a_v2 请求、业务状态、hex 音频 | 改编协议；不静默换默认 voice | `tts/minimax.py` |
| [tts/_base.py](https://github.com/jianchang512/pyvideotrans/blob/ad0b8bd8ec8a3cdceaab4358fd7fdc1ea19b3800/videotrans/tts/_base.py)、[configure/base.py](https://github.com/jianchang512/pyvideotrans/blob/ad0b8bd8ec8a3cdceaab4358fd7fdc1ea19b3800/videotrans/configure/base.py#L96-L116) | 逐段调度、统一 WAV | 参考；`convert_to_wav` 实际定义在 BaseCon | `tts/base.py`、`dubbing.py`、`media.py` |
| [util/help_role.py](https://github.com/jianchang512/pyvideotrans/blob/ad0b8bd8ec8a3cdceaab4358fd7fdc1ea19b3800/videotrans/util/help_role.py)、[voicejson/](https://github.com/jianchang512/pyvideotrans/tree/ad0b8bd8ec8a3cdceaab4358fd7fdc1ea19b3800/videotrans/voicejson) | 动态 ElevenLabs 声音、MiniMax 语言分组与站点映射 | helper 仅参考；MiniMax 数据为后续提取候选 | `tts/*`、`config/minimax_voices.json` |
| [task/_stage_align.py](https://github.com/jianchang512/pyvideotrans/blob/ad0b8bd8ec8a3cdceaab4358fd7fdc1ea19b3800/videotrans/task/_stage_align.py) | 调用 SpeedRate 并重写目标字幕 | 参考阶段边界，保留原始时间字段 | `alignment.py`、`subtitles.py` |
| [task/_rate.py](https://github.com/jianchang512/pyvideotrans/blob/ad0b8bd8ec8a3cdceaab4358fd7fdc1ea19b3800/videotrans/task/_rate.py)、[docs/Synchronize.md](https://github.com/jianchang512/pyvideotrans/blob/ad0b8bd8ec8a3cdceaab4358fd7fdc1ea19b3800/docs/Synchronize.md) | slot、Both、Rubber Band / atempo、局部慢放、拼接、最终时间轴 | 优先改编纯计算；媒体执行与失败传播重新隔离 | `alignment.py`、`media.py` |
| [util/help_ffmpeg.py](https://github.com/jianchang512/pyvideotrans/blob/ad0b8bd8ec8a3cdceaab4358fd7fdc1ea19b3800/videotrans/util/help_ffmpeg.py)、[util/_ffmpeg_runner.py](https://github.com/jianchang512/pyvideotrans/blob/ad0b8bd8ec8a3cdceaab4358fd7fdc1ea19b3800/videotrans/util/_ffmpeg_runner.py)、[util/_ffprobe.py](https://github.com/jianchang512/pyvideotrans/blob/ad0b8bd8ec8a3cdceaab4358fd7fdc1ea19b3800/videotrans/util/_ffprobe.py)、[util/_ffmpeg_audio.py](https://github.com/jianchang512/pyvideotrans/blob/ad0b8bd8ec8a3cdceaab4358fd7fdc1ea19b3800/videotrans/util/_ffmpeg_audio.py) | 子进程、探测、16 kHz ASR 音频、concat、变速 | facade 不复制；必要函数去全局依赖后改编 | `media.py` |
| [util/help_srt.py](https://github.com/jianchang512/pyvideotrans/blob/ad0b8bd8ec8a3cdceaab4358fd7fdc1ea19b3800/videotrans/util/help_srt.py)、[util/_srt_parse.py](https://github.com/jianchang512/pyvideotrans/blob/ad0b8bd8ec8a3cdceaab4358fd7fdc1ea19b3800/videotrans/util/_srt_parse.py)、[util/_srt_ass.py](https://github.com/jianchang512/pyvideotrans/blob/ad0b8bd8ec8a3cdceaab4358fd7fdc1ea19b3800/videotrans/util/_srt_ass.py) | 毫秒格式、SRT 输出、ASS 样式 | 参考；直接从统一 Segment 写 SRT / ASS | `subtitles.py` |
| [task/_stage_subtitle.py](https://github.com/jianchang512/pyvideotrans/blob/ad0b8bd8ec8a3cdceaab4358fd7fdc1ea19b3800/videotrans/task/_stage_subtitle.py)、[task/_stage_assemble.py](https://github.com/jianchang512/pyvideotrans/blob/ad0b8bd8ec8a3cdceaab4358fd7fdc1ea19b3800/videotrans/task/_stage_assemble.py) | ASS hard burn、音视频装配、cwd 路径处理 | 仅保留单语言硬字幕与目标配音输出 | `render.py`、`subtitles.py`、`media.py` |

## 4. Pipeline 的实际组织

`TransCreate` 是承载 cfg、source_srt_list、target_srt_list、queue_tts 等数据的 dataclass，通过多个 mixin 提供阶段方法；不是单一文件中的完整顺序流水线。CLI 的视频翻译分支实际顺序为 prepare → recogn → diariz → trans → dubbing → align → assembling → task_done；GUI 路径在 `task/job.py` 通过不同 Worker 和全局队列移交任务。

本 Skill 保留 prepare / recogn / trans / dubbing / align / assemble 的媒体职责，移除 diariz；在 recogn 与 trans 之间插入 Agent 文件润色及 Python 校验。每条 CLI 命令加载 manifest、执行一个阶段、验证产物、落盘新状态并退出。Python 不执行 input()，也不试图跨越 Agent 润色步骤自动调用另一个模型。

## 5. Faster Whisper 与 resegment

上游 `faster_whisper()` 使用 WhisperModel(local_dir, device, compute_type)。`device_name=auto` 在包装层依据 is_cuda 选择 cuda / cpu；模型加载失败时按 CUDA float16 或 CPU int8，再到 float32 尝试。不能把所有模型缺失或 CUDA 故障都包装成精度类型重试。

识别直接传完整音频：beam_size、VAD、word_timestamps=True、language=None（auto）或语言基础码。该提交 VAD 参数包含 min_silence_duration_ms=2000、min_speech_duration_ms=0、threshold；我们只暴露必要参数，保留具体解析结果到任务配置。`segments` 是需要消费的迭代结果，消费过程中也可能失败，不能提前标记 TRANSCRIBED。

`_resegment` 利用字词时间戳，在标点、静音间隔和时长条件下切分，并合并短段；对字母语言与无空格语言采用不同连接方式。当前源码含 max_speech_ms 之外的 1500ms 弹性范围，不能把其命名误读为严格最大值。它还包含调试 print，以及末尾短句访问前一段的路径；改编前必须用仅一条短语、缺 words、空结果等夹具检查，不能原样搬入。

所有 resegment 都发生在 segments_raw.json 定稿之前。之后 id、顺序、段数、start_ms、end_ms 冻结。关闭 word_timestamps 时保留模型原段，不尝试凭空生成词级边界。统一在 ASR 边界转换整数毫秒，并测试转换的单调性。检测语言写入 manifest，不写上游 TEMP_ROOT 全局文件。

## 6. Microsoft Edge 翻译（仅历史参考）

v1.2 已删除 Microsoft 运行时适配器；当前技能由 Agent 本地生成译文，Python 只做结构校验。下述内容仅保留为 v1.0 的上游研究记录，不是当前执行路径。

当前上游 POST `https://edge.microsoft.com/translate/translatetext`，query 为 from 空值、to 目标码、isEnterpriseClient=false，JSON body 为字符串数组；不是 Azure SDK，也不是 Azure 的对象数组协议。响应逐项读取 `translations[0].text`。上游将 zh-cn / zh-tw 映射为 zh-Hans / zh-Hant。

我们采用 `MicrosoftTranslator` 协议与 `MicrosoftEdgeTranslator` 实现，TLS 校验开启。第一版保持上游 from 空值自动检测的请求行为；用户源语言用于 ASR，manifest 同时记录检测结果。语言映射集中在 languages.py，避免在 adapter 内重复转换。

上游 `_item_task` 先把结果用换行合成字符串，BaseTrans 再按换行拆开；数量不足补空行，多余行截断。这会混淆“一个译文内部换行”和“多个 segment”。本项目保持数组到数组，逐批验证长度、元素结构及非空译文，再按原 ID 顺序写回；数量错误属于响应技术错误，可有界重试，最终失败时保留已完成批次，不能补空行伪造成功。缓存必须包含文本、目标码、adapter 版本与顺序。

## 7. 两家 TTS 与音色

ElevenLabs 上游使用 SDK 的 text_to_speech.convert：text、voice_id、model_id、output_format=mp3_44100_128 与 VoiceSettings，消费返回字节后转 WAV。help_role 中实际动态调用为 client.voices.get_all()；结果保存 name / voice_id，但通过清洗后的 name 作字典键，可能覆盖重名。它的默认缓存分支也不能代替我们的动态加载要求。

本项目在 voices 阶段动态读取，并以 provider + voice_id 唯一标识；保留显示名称，不把名称当稳定主键，不依赖 Rachel / Adam 等示例名恒定存在。SDK 版本与分页能力在 Phase 4 按官方接口再次核对。

MiniMax 上游 POST `/v1/t2a_v2`，Bearer 认证，model / text / stream=false / voice_setting / language_boost / audio_setting；当前代码请求 44100 Hz 单声道 WAV，并解析 base_resp.status_code 和 data.audio 的 hex 字节。随后共用转换函数生成 48000 Hz、双声道、pcm_s16le WAV。

上游有 minimaxi.json 与 minimaxiio.json 两套按语言分组的 name → voice_id 数据，help_role 根据 api.minimax.io 切换。我们拟提取到自己的 config/minimax_voices.json，保留站点和语言维度，去掉 No。不移植找不到声音就回退 male-qn-qingse 的行为。host、model、key 从指定环境变量读取；host 统一规范化为 HTTPS origin，避免重复拼接协议或路径。

两家都逐 Segment 合成。TTS 原始输出只作为阶段中间件，alignment 只消费已验证的统一 WAV 与真实时长。TTS 请求中 speed 保持正常值，变速由 alignment 统一承担，防止两处叠加。MiniMax 不沿用硬编码 happy 情绪。voice 列表快照保存到 job，第二次暂停的编号直接映射到 provider + voice_id。

## 8. Both 模式：确认采用的计算规则

直接证据：[阈值与配置](https://github.com/jianchang512/pyvideotrans/blob/ad0b8bd8ec8a3cdceaab4358fd7fdc1ea19b3800/videotrans/task/_rate.py#L215-L261)、[三种模式分支](https://github.com/jianchang512/pyvideotrans/blob/ad0b8bd8ec8a3cdceaab4358fd7fdc1ea19b3800/videotrans/task/_rate.py#L395-L431)。

slot 从当前 start 到下条 start，最后一条到视频结束，吸收两段之间的静音。第一段之前的视频保持原速度，目标音轨补同长前导静音。

设 S 为 slot 时长、D 为原始 TTS 实测时长，单位毫秒：

| 条件 | 目标 slot 时长 T | 音频动作 | 视频动作 |
|---|---:|---|---|
| D ≤ S | S | 原速，尾部补 S−D 静音 | 原速 |

例如 S=2000、D=8000：T=5000，音频速度 1.6，视频 PTS 倍率 2.5。继续执行，不新增用户确认。


未来若启用单侧技术保护，则由另一侧确定性承担剩余时长；这不属于本轮实现范围，也不产生第三个交互节点。当前公式在忽略整数毫秒舍入时，Both 大倍率分支音频加速因子为 2D/(S+D)，趋近于 2，较大的时长扩张主要由视频承担。这是公式推导，不是主观质量保证。

## 9. 执行层：变速、拼接与最终时间轴

上游先 prepare → calculate → audio speed → video speed/concat → audio concat。Rubber Band 需要 pyrubberband 与可执行程序同时存在；不存在时选 FFmpeg。`_precise_speed_up_audio` 将超过 2 的倍数拆成多个 atempo，并统一 48000 Hz / 2ch / PCM。现代 [FFmpeg atempo](https://ffmpeg.org/ffmpeg-filters.html#atempo) 接受范围不应被旧注释简化为只能到 2；本项目仍按需求分解 >2 的倍率。

视频通过按 slot 裁切并 setpts 处理，再 concat。上游会合并连续 pts=1 的裁切任务以减少片段数；我们保留这个优化，但不合并 Segment。仅需要慢放的区域改变速度，全无慢放时直接使用原视频流作为渲染输入。混合片段应统一编码参数与时间基准后拼接，不能假设任意 stream copy 切点都准确。

上游 `_cut_video_get_duration` 带 PTS 经验偏置（慢段 +0.009999999、原速段 1.003999000）；我们不直接照搬固定偏置。拟采用归零 PTS、基于帧边界的确定性量化、探测实际输出时长，使计划与执行误差显式可见。具体帧率策略和容差在 Phase 6 的真实 FFmpeg 验证中确定，不在调研阶段宣称已精确到毫秒。

上游 `_concat_audio_aligned` 重读变速后 WAV，用累计音频长度更新 start_time / end_time，短段尾部补静音；字幕结束对应配音结束，而非整个含静音的 slot。`_stage_align` 随后保存更新后的目标字幕。上游并没有把每一段实测视频长度重新回写成最终字幕边界，不能仅探测总时长就认定局部无漂移。

本项目设计：保留原始 start_ms/end_ms；另存计划 slot 与实测媒体边界。final_start_ms 为实际 slot 起点，final_end_ms 为实际配音终点；下条起点包含本 slot 的尾部静音。帧量化后的实际 slot 边界作为拼接依据，音频对同一边界做自动微调或补静音，全部由 Python 决定。最终 SRT / ASS 只消费 final_*。累计偏移例子见架构文档。

以下上游行为不复用：缺失 TTS 生成占位静音、无效视频片段跳过、变速失败后以未变速裁切当成功、音频 future 异常吞掉、覆盖原始 TTS、完成后立即删除恢复用片段。这些是技术执行结果处理，与倍率质量门槛无关。本项目有界自动 fallback 后仍失败才记录 FAILED；不截掉内容或遗漏片段来制造 DONE。

## 10. SRT、ASS、Windows 与最终装配

help_ffmpeg / help_srt 目前是 facade，实现在拆开的 util 文件。FFmpeg runner 使用参数数组、check=True、cwd，以及 Windows CREATE_NO_WINDOW；保留这些调用习惯，去掉 app_cfg、全局队列、任意全局命令附加参数。

上游 subtitle 阶段先生成 SRT，再调用 set_ass_font 转 ASS 和替换样式；最终返回 basename。assemble 将缓存目录作为 cwd，滤镜只使用该 basename，避免把 Windows 盘符直接塞入滤镜字符串。我们采用同样的边界处理：job/render/target.ass 固定文件名，子进程 cwd 指向其目录；输入、输出媒体作为独立绝对路径参数。shell quoting、FFmpeg filter escaping、concat 文件 escaping 是三个不同层次，不能把反斜杠全部替换为正斜杠就认定完成。

第一版直接写 ASS，样式来自 config 的 font / font_size / margin_v / outline / alignment；SRT 毫秒、ASS 厘秒分别序列化。换行和 ASS 控制符需转义，Unicode 文本不得丢失。保留 target.srt、target.ass、target.wav；MP4 为处理后视频 + 目标配音 + 单语言 ASS 硬字幕，不混入源语音。

Windows 验收必须实际覆盖盘符、反斜杠、空格、中文路径，包含 job 根目录本身带空格与中文的场景。单元测试转义字符串不能替代真实 FFmpeg 烧录。最终还需检查音视频流、字幕帧、时长与输出存在性，不只看进程返回 0。

## 11. 本轮证据与实施留项

已完成：固定提交源码阅读、指定能力映射、Both 行为核验、基础库/产品范围交叉对照、模块和恢复设计。当前模型、网络与媒体实际验收状态统一记录在 [VALIDATION](VALIDATION.md)，不把这里的源码研究当作 live 成功。

