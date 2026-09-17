# 当前验收状态

## 2026-09-13：2.1.1 客户端清理策略

当前仅支持 local 清理；cleanup_policy 和 alignment_policy 分阶段调用 VideoTranslatePolicyV1，仅发送元数据，不上传视频或音频。下方历史验收记录不代表当前完整边界。

- 121组与原n8n清理策略的差分案例通过，含唇泥视频真实OCR元数据；离散决策完全一致，数值使用1.1e-4容差验证。
- 30组对齐策略与原工作流完全一致；工作流拒绝cleanup_policy，已验证成功与失败回调。
- 167项默认测试通过，1项live测试排除；使用现有含libass的/Users/matthew/.local/bin/ffmpeg。Homebrew另一份FFmpeg缺少ASS滤镜，会导致2项原有媒体测试失败。
- 新增测试验证本地清理不读取Lingzhi Key、不提交网络任务、保护场景文字及支持空轨道/关闭检测。
- 状态枚举与产物协议未改；已完成阶段保持原有幂等/指纹恢复。此次没有线上部署、真实付费TTS或策略调用。

## 历史：2.1.0


## 2.1.0 双清除模式

- `policy_client.py` 只通过 LZStudio CLI 提交任意任务并轮询同一 task ID；不直接请求 n8n。
- n8n 工作流不增加鉴权节点，鉴权由 CLI / 上层任务服务负责。
- 服务端只有输入标准化和确定性 Code 策略，不包含 GPT、Gemini、Claude 等 LLM 节点，因此策略计算的 LLM Token 成本为 0。
- 清理阶段只发送结构化 OCR Track、ASR 时间段和视频元信息；不上传视频、图片、音频或 OCR 原始帧。服务端返回文字类别、清理资格和字幕布局。
- 对齐阶段只发送视频时长、Segment ID/起点和实测 TTS 时长；服务端返回 `AlignmentPlan`，客户端据此执行音频变速及必要的视频时长调整。
- 客户端不存在与上述两个策略等价的 fallback；服务端策略不可用时任务明确失败，避免客户绕过服务端继续完整运行。
- 客户端配置不保存服务端策略阈值。

## 本地执行层回归范围

- OCR：本地 RapidOCR 抽帧、解析、Track 聚合。
- 清理：仅根据服务端批准的 Track 生成动态 Mask，并执行 OpenCV 修复。
- 翻译：由当前 Agent 完成，Python 只做结构校验。
- TTS：MiniMax / ElevenLabs 使用客户自己的 Provider 凭据。
- 对齐：只执行服务端返回的计划，不重新计算“是否拉长视频/拉长多少”。
- 字幕：根据服务端返回布局生成 SRT/ASS，本地保留样式、换行和背景渲染。
- 渲染：FFmpeg 合成、流检查、完整解码和源文件指纹保护。

## 发布检查

发布前至少执行：

1. `python -m compileall video_translate scripts tests`；
2. `python scripts/release.py scan`；
3. n8n JSON 语法校验；
4. 对 n8n 两种 action（`cleanup_policy` / `alignment_policy`）运行样例契约测试；
5. 在具备 LZStudio CLI 的目标环境执行一次真实 submit/fetch 联调；
6. 在具备 FFmpeg/OCR/ASR/TTS 依赖的目标机器执行完整媒体回归。

## 限制

当前源码包不内置 LZStudio CLI 二进制；运行环境需通过 `LZSTUDIO_CLI` 指定路径，或让 `lzstudio` 位于 PATH。`LINGZHI_API_KEY` / `LZSTUDIO_API_KEY` 仅供 CLI 使用，不写入 n8n 工作流。服务端工作流文件应由运营方保留，不随面向客户分发的 Skill ZIP 一起发放。

- 新增单测覆盖：第三方模式仍获取远端字幕布局、但不执行本地 mask/inpaint；LZStudio upload 契约；任意任务工作流 ID 与 result_url 契约。

## 2026-09-13：2.2.0 GPU STTN 自动清理

- 完整非 live 测试：184 passed，1 live deselected；CUDA/MPS 优先级、无 GPU、缺失依赖、推理失败、超时、内存不足减半两次、缓存损坏和三方模式隔离均覆盖。
- 本机真实 MPS：眼妆源视频 720×1280、30fps、1169 帧、38.966667 秒；自动选中 STTN/MPS，16 段，未回退。完整解码、帧数、时长与源 SHA-256 检查通过。
- 22.4px 基准（本片实际 40px）越南语 ASS 渲染及完整解码通过；这是字幕渲染验证，不重新生成或消费 TTS/远端对齐任务。
- 同一完整视频缓存恢复验证：不重复任何分段 GPU infer 请求，只执行设备 probe；所有 1169 帧再次导出并检查。
- Windows CUDA 已覆盖模拟分支；未在 Windows NVIDIA 实机执行，不能将其报告为实机验证。

## 2026-09-13：2.2.1 服务授权输出

- 灵智服务边界统一中性授权错误，固定管理员提示；屏蔽账户元数据，不新增账户查询或本地统计。客户自有 TTS 错误说明保持原行为。
- 相关离线测试 49 项通过，覆盖 CLI 输出、任务状态、停止提交、轮询、普通超时及产物恢复；Skill 结构校验通过。
- 完整默认测试：190 项通过，2 项渲染失败，1 项 live 测试排除。本机 FFmpeg 9.0.1 缺少 ASS/subtitles 滤镜；本次未修改渲染实现，未调用真实外部服务。
