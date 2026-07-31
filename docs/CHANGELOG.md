# Changelog

lada-agent 的可见变更记录。格式遵循 [Keep a Changelog](https://keepachangelog.com/)。

## [Unreleased]

## [0.3.0] - 2026-07-31

### Added
- Web/Tk 设置页增加 Lada CLI 能力探测，动态读取设备、模型、编码 preset、encoder 和 encoder options。
- 增加 `lada_options.extra_cli_args` 高级覆盖层，参数最后追加且不经过 shell；任务输入输出和信息类命令受保护。
- 增加 Lada 0.11 参数契约测试，覆盖 auto 默认值、FP16 三态、布尔负向 flag、覆盖优先级和输出模板校验。
- 增加无头环境诊断、启动前 preflight、结构化版本兼容状态和最终 argv 预览。
- 增加显式短视频冒烟测试：FFmpeg 截取临时样本、真实调用 Lada、ffprobe 验证输出。
- 冒烟测试增加可配置的分辨率和帧率上限，默认 640px/5fps，`0` 可保留源规格。
- 增加 `lada_profiles`、`directory_profiles` 和 Web 单次模板任务提交。
- 增加 Linux Web 启动脚本及 systemd user unit 示例。

### Changed
- 包版本提升至 0.3.0，默认可执行文件名改为跨平台的 `lada-cli`。

### Fixed
- 默认设备和编码 preset 改为 `auto`，不再破坏 Intel XPU/QSV、CPU 或开发版 MPS 的上游动态选择。
- FP16 开启和关闭均显式传参；输出模式强制每输入唯一占位符与扩展名。
- 修正“检测脸部马赛克”的界面语义，并同步 Lada 支持的视频扩展名。
- Web API 不再把自定义检测/修复模型长路径截断到 128 字符。

## [0.2.0] - 2026-07-18

### Added
- 粉白 Web 仪表盘与本地生成背景图，Overview 增加历史、处理中/等待大小、总视频大小、吞吐和 ETA 指标。
- Video review 增加 Ready / Partial / Missing 状态、搜索选择、批量删除历史与 Clean missing；历史清理永不删除媒体文件。
- Original / Restored 并排播放、Swipe compare、全屏比较、同步播放/seek、共同缓冲提示和 HTTP Range 私有缓存。
- Web 登录 session 密钥持久化，并支持 `LADA_AGENT_SECRET_KEY` / `LADA_AGENT_SECRET_KEY_FILE`。
- CLI 增加 `--help`、`--version`、`--config`、`--init-config` 和安全覆盖配置的 `--force`。
- 增加 `LADA_AGENT_CONFIG` 环境变量，便于 Web/GUI 服务和隔离测试选择配置文件。
- CLI、Web 和 GUI 增加基于配置文件的跨进程 monitor 单实例锁。
- Socket.IO client 与 Remix Icon 固定版本随包分发，LAN 断网时不再依赖 CDN。
- 增加 MIT `LICENSE`、第三方资源许可清单和 LADA CLI 兼容性文档。
- 增加发布合同测试，覆盖配置初始化、跨进程锁、离线静态资源和关键视频 UI 合同。
- 增加 Windows/Linux 本地全量检查脚本、GitHub Actions 双平台 CI 和跨平台行尾规则。

### Fixed
- 序列化 Web monitor 启停与配置重启；重启会终止并等待旧任务，拒绝重叠实例。
- Socket.IO 连接复用远程 token/session 认证，并拒绝跨站状态变更请求和 URL 查询参数 token。
- 配置和处理历史改用同目录临时文件 + `fsync` + `os.replace` 原子写入。
- 历史清理在同一把锁内按 key 删除，不再以旧快照覆盖并发完成记录。
- LADA 返回成功时验证预期输出存在；移动原文件后记录最终路径。
- `output_directory`、`output_file_pattern`、`scan_interval`、`auto_start` 现在具有实际运行语义。
- Watchdog 回调只提交候选任务；就绪检测和哈希不再阻塞事件线程，未变化文件复用哈希。
- 移除重复的 Hatchling `force-include`，恢复 wheel/sdist 构建。

### Changed
- 无显式 `output_directory` 时保留各输入目录下 `Restored` 的兼容行为。
- 更新仓库内 `lada-cli.exe --help` 快照和远程访问文档。
- Linux/macOS 前台与后台脚本迁移到 `uv sync --locked` / `uv run lada-monitor`。
- `max_clip_length` 文档与设置标签修正为帧数；`mp4_fast_start` 改为准确描述传参语义。
- wheel/sdist 版本更新为 0.2.0，并补齐许可证、作者、关键词和分类元数据。

## [0.1.0] - 2026-07-11

### Added
- 标准 `src/lada_agent/` Python 包布局
- 三个 console 脚本入口,由 `pyproject.toml` 的 `[project.scripts]` 暴露:
  - `lada-monitor`(命令行监控守护)
  - `lada-web`(浏览器管理界面,支持 `--host` `--port` `--debug` `--version`)
  - `lada-gui`(Windows Tkinter 桌面面板)
- `lada_agent.__version__ = "0.1.0"` 与 `lada-web --version`
- `[dependency-groups] dev = ["pytest>=8", "ruff>=0.6"]` 与 `[tool.uv] default-groups = []`
- `[tool.ruff]` 与 `[tool.pytest.ini_options]` 配置
- Flask 的 `templates/` 与 `static/` 作为 `src/lada_agent/` 包数据进入 wheel,`pip install` 后无需手动放置
- `docs/README.md` 文档目录索引
- `docs/CHANGELOG.md`(本文档)与按 Keep a Changelog 格式填写的初始条目

### Changed
- 扁平 `src/*.py` 全部移入 `src/lada_agent/`,内部 import 改为 `from lada_agent.X import ...`
- `pip install -r requirements*.txt` → `uv sync`(`requirements.txt` 与 `requirements_web.txt` 已删除)
- 所有 `.bat` 启动脚本（包括 `scripts/start_gui.bat`）切换到 `uv run` 风格
- Web 访问日志路径修复:`script_dir/../logs/web_access.log` → `os.getcwd()/logs/web_access.log`,无论源码运行还是 `pip install` 后都一致
- `packaging/LadaAgent.spec` 与 `scripts/build_exe.bat` 加入模板、静态资源和隐藏模块配置
- 文档:`docs/usage.md` 改为真正讲 `lada-agent` 用法;老的 `lada-cli.exe --help` 抓取文本挪到 `docs/upstream/lada-cli-help.txt`;`docs/README_WEB.md` 全面重写
- `tests/test_core.py` 内部 Web 相关测试改用 `pytest` fixture + `monkeypatch.setattr`,不再 monkey-patch 模块全局
- `src/lada_agent/lada_monitor.py` 改用 `dict[str, ...]` / `list[str]` / `int | None` 等 PEP 604/585 现代类型注解,`ruff` 全套规则通过
- 顶层 `src/lada_agent/` 目录从 `.gitignore` 的"忽略整目录"改为源包目录,被 Git 跟踪

### Removed
- `main.py` —— `uv init` 留下的 `Hello from lada-agent!` 占位
- `requirements.txt`、`requirements_web.txt` —— 依赖信息已统一进 `pyproject.toml`
- `src/start_web.py` —— 逻辑吸收进 `src/lada_agent/web.py`
- `lada_gui.py` 顶部的 `try/except + sys.path.append` 工作区(包安装后无需)
- `.gitignore` 中 `src/lada_agent/` 与顶层 `lada_agent/` 的整目录忽略条目(改用更精确的 `*.log` 与 `processed_files.json`)

## [pre-0.1.0]

详见 [`AUDIT_2026-05-12.md`](AUDIT_2026-05-12.md) 的"已修复问题"章节 —— 它同时承担了 0.1.0 之前一段较长时期的安全与稳定性修复记录。
