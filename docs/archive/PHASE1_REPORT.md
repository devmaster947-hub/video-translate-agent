> 历史归档：仅记录 Phase 1，不能作为当前执行依据。当前规则见 [PIPELINE](../PIPELINE.md)，验收见 [VALIDATION](../VALIDATION.md)。

# Phase 1 交付报告

日期：2026-09-09。范围：models、state、config、CLI 基础、media；未进入 Phase 2。

## 实现内容

- Pydantic Segment / Voice / Artifact / Manifest：严格整数时间、字段约束、路径约束、冻结记录和失败状态一致性。
- JobStore：独立 job、SHA-256、manifest 原子替换、系统写锁、相邻阶段转换、FAILED 保留成功状态并恢复、完整性校验。
- Config：明确项目路径基准，YAML 公共配置、环境凭据、Key 不序列化、不进入 repr 或 manifest。
- CLI：preflight / create / status，UTF-8 JSON、正确退出码、跨 cwd 运行；未注册未来命令。
- Media：ffprobe、工具能力检查、16kHz 单声道 ASR 音轨、48kHz 双声道 PCM WAV、全部源音轨移除；保留源文件、拒绝覆盖、参数数组和 shell=False。

## 新增文件

| 范围 | 文件 |
|---|---|
| 项目配置 | pyproject.toml、.gitignore、.env.example |
| 运行配置 | config/default.yaml |
| 包与五个模块 | video_translate/__init__.py、models.py、state.py、config.py、cli.py、media.py |
| CLI 入口 | scripts/video_translate.py |
| 正式测试 | tests/conftest.py、test_models.py、test_state.py、test_config.py、test_cli.py、test_media.py |
| 文档 | docs/PIPELINE.md、docs/PHASE1_REPORT.md |


## 测试结果

最终执行 `python -m pytest -q`：**62 passed in 12.67s**，无失败、无警告、无跳过。使用 Windows、Python 3.12.10、Pydantic 2.13.4 和本机 FFmpeg / ffprobe。

覆盖五个模块，包含真实 FFmpeg 验证：中文/空格/单引号路径，含两条音轨的原视频，音频提取、统一 WAV、全部原音轨移除、源文件 SHA-256 不变、损坏文件/无音轨/覆盖拒绝，以及不同 cwd 下 create → 新进程 status。生成媒体为正式测试夹具，不是真实翻译成品。

状态测试包含原子替换失败保留旧 manifest、单写锁、失败恢复、完整阶段顺序、拒绝跳阶段、拒绝修改已确认选择和源/产物篡改检测。配置测试覆盖零、一、两家 Key，空白 Key 和敏感输入不外泄。

实际 preflight：通过；ffmpeg、ffprobe、subtitles、atempo、libx264、AAC 可用，Rubber Band 未找到（可选）。本机检测到两家 Provider 均配置凭据，但没有调用 API，未验证远端权限和声音可用性。full_pipeline_ready=false。

## 边界与未实现能力

最终媒体产品边界已写入当前文档：去掉原人物讲话、BGM、环境声、音效；只保留画面 + 目标 TTS + 目标 ASS。不引入人声分离、Demucs、BGM 混音或恢复。

Phase 2–8 均未实现：ASR/resegment/polish validator、语言映射、Microsoft、Provider/voices、逐段 TTS、Both 对齐、时间轴重建、字幕/渲染、SKILL 和完整端到端。Phase 1 状态机包含未来状态，仅提供存储协议，不能用状态转换测试宣称未来业务成功。

当前媒体输出原子发布使用本地硬链接，NTFS 已通过；其他不支持硬链接的文件系统将明确失败。video_without_audio 复制视频流，输入 codec 不能封装进 MP4 时明确失败，未增加隐式转码。manifest 原子写入保护文件替换，不宣称能消除磁盘损坏或所有断电风险。

未运行 API live 测试，未下载 Whisper 模型，未安装正式 Skill，未生成完整翻译视频。本阶段没有 Git commit 请求，也未提交版本。

收尾检查未发现临时 debug、一次性验证脚本或无用途功能占位；正式 mock/合成媒体测试保留。自动审批检查拒绝删除项目内 __pycache__ / .pytest_cache 的操作，返回理由仅为 blocked by policy；缓存仍可能存在，已被 .gitignore 排除，不作为源码交付。
