# Windows 源码 Skill 发布

版本 2.3.5 marketplace（本地媒体处理无固定总时限）。产物是完整 Skill 目录的 ZIP，不是 exe，不包含 Python、FFmpeg、OCR 或 ASR 模型。许可证和第三方声明随包保留。依赖范围由 pyproject.toml 指定；不是锁定全部传递依赖的离线安装包。STTN 权重与两个平台 LZStudio CLI 作为同版本 GitHub Release 独立资产发布，客户端按平台下载并验证固定 SHA-256。

## 发布命令

依次运行完整默认 pytest、Skill validator、本地 preflight、Git 状态/文件检查、`python scripts/release.py scan`。本项目不是 Git 工作目录时记录此事实，改用文件检查，不创建或提交仓库。

`python scripts/release.py build` 从固定根文件和脚本/包/配置/文档/测试的文本扩展名白名单创建 `dist/staging-v2.3.5-marketplace/video-translate-agent`，随后压缩。存在 staging 或同名 ZIP 时拒绝覆盖；维护者应先检查并将旧发布归档后再重建。

工具再次解压并检查 CRC、路径、关键文件、禁入目录、凭据扫描和逐文件字节一致性，输出 ZIP 与 SHA-256 文件。扫描属于启发式检测，需要人工审查变量名、请求头构造和测试假数据；不读取环境变量、不输出疑似值。只扫描白名单源码，不接触真实 jobs/output。

## 独立安装验收

将 ZIP 分别解压到新的 Windows 和 macOS 临时目录，确认未夹带 `.env`、`.venv`、jobs、output、媒体、日志或缓存。清除子进程的两家 Key 环境变量（不读取值），运行 `install.ps1` 或 `install.sh`，创建全新 `.venv` 并安装 `.[test]`。无 ElevenLabs Key 时向导应自动打开；取消后 preflight 仅允许 `ELEVENLABS_CREDENTIAL_REQUIRED` 提示，其他失败必须处理。

从项目目录之外，用副本 `.venv/Scripts/python.exe -I` 导入 video_translate，断言 `__file__` 位于解压副本；执行副本 CLI `--help`。检查无 Key 的 preflight 行为，并用明确的测试占位变量检验本机工具/依赖就绪分支，不将其视作远端 API 验证。安装后的 .venv/egg-info 和 preflight 创建的运行目录仅在临时副本中产生，不回填 staging 或 ZIP。

完整默认 pytest 包含真实 FFmpeg 测试；TTS live 测试默认排除，发布流程不消耗 API。分发后仍须在目标电脑配置自己的至少一家 TTS API Key 并验证其权限。


服务端工作流不打入客户 Skill ZIP。发布时单独部署 `VideoTranslatePolicyV1.n8n.json`。
