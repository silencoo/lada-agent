# LADA视频文件自动监控程序

这是一个用于自动监控指定目录并对新发现的视频文件执行LADA demosaic处理的程序。

## 功能特点

- 🔍 **实时监控**: 监控指定目录的文件变化，自动检测新视频文件
- 🚀 **自动处理**: 发现视频文件后自动调用lada-cli.exe进行demosaic处理
- ⚙️ **灵活配置**: 支持多种参数配置，包括监控目录、输出目录、LADA选项等
- 📝 **详细日志**: 记录处理进度、错误信息和处理历史
- 🛡️ **防重复**: 避免重复处理同一文件
- 🧵 **并发控制**: 支持限制同时处理的视频文件数量
- 🪟 **跨平台**: 支持Windows和Linux系统
- 🌸 **LAN Web UI**: 粉白仪表盘、视频状态筛选、历史清理、并排/滑动对比与全屏同步播放
- 🔒 **运行保护**: 同一配置只允许一个 monitor 进程，远程访问使用 token + 持久化登录会话

## 系统要求

- Python 3.12+(由 `pyproject.toml` 锁定)
- [uv](https://github.com/astral-sh/uv)(推荐;负责创建虚拟环境并解析依赖)
- lada-cli.exe (需自行下载和配置)

当前兼容性基线见 [`docs/LADA_COMPATIBILITY.md`](docs/LADA_COMPATIBILITY.md)。本仓库已验证 LADA CLI `0.10.1`；`0.11.0` 是下一目标版本，升级前建议先跑一轮真实视频回归。

## 安装步骤

1. **下载或复制项目到本机并进入目录**
   ```bash
   cd lada_agent
   ```

2. **安装依赖并创建虚拟环境**
   ```bash
   uv sync
   ```

3. **配置lada-cli.exe**
   - 下载lada-cli.exe并放置在合适位置
   - 确保lada-cli.exe在系统PATH中或在config.json中指定完整路径

4. **编辑配置文件**
   先生成`config/config.json`，再填写本机路径：

   ```bash
   uv run lada-monitor --init-config
   ```

   也可以手动复制`config/config_example.json`。配置结构如下：

   ```json
   {
     "monitor_directories": [
       "C:\\path\\to\\monitor1",
       "C:\\path\\to\\monitor2"
     ],
     "output_directory": "C:\\path\\to\\output",
     "lada_cli_path": "C:\\path\\to\\lada-cli.exe",
     "supported_extensions": [".mp4", ".avi", ".mkv", ".mov"],
     "lada_options": {
       "device": "cuda:0",
       "fp16": true,
       "encoding_preset": "hevc-nvidia-gpu-hq",
       "mp4_fast_start": true,
       "mosaic_restoration_model": "basicvsrpp-v1.2",
       "max_clip_length": 180,
       "mosaic_detection_model": "v4-fast",
       "detect_face_mosaics": false
     },
     "max_concurrent_processes": 2,
     "log_level": "INFO",
     "uptime_kuma": {
       "enabled": false,
       "push_url": "https://uptime.example.com/api/push/your-key",
       "interval_seconds": 60,
       "status": "up",
       "message": "LADA Agent online"
     }
   }
   ```

## 配置说明

### 基本配置

- `monitor_directories`: 要监控的目录列表
- `output_directory`: 处理后文件的统一输出目录；留空时使用各输入目录下的 `output_directory_name` 子目录
- `lada_cli_path`: lada-cli.exe的完整路径
- `supported_extensions`: 支持的视频文件扩展名
- `scan_interval`: Watchdog 事件之外的恢复扫描间隔（秒），用于捕捉漏失的文件事件

### LADA处理选项

> 字段白名单由 [`config_manager.LADA_OPTION_SCHEMA`](src/lada_agent/config_manager.py) 限制;Web 端 `/api/config/update` 会丢弃未识别字段,数值字段会按边界收敛。

- `device`: 计算设备 (`"cpu"` / `"cuda:0"` 等)
- `fp16`: 是否启用 FP16 半精度推理 (默认 `true`)
- `encoding_preset`: LADA 内置编码预设(如 `"hevc-nvidia-gpu-hq"`)
- `encoder` / `encoder_options`: 自定义 ffmpeg 编码器及参数(设置后**优先于** `encoding_preset`);`encoder_options` 作为单个字符串传入(例如 `"-crf 20 -preset slow"`)
- `mp4_fast_start`: 向 LADA 传递 Web 播放优化选项；最终 `moov`/fragment 布局仍取决于编码器和容器
- `mosaic_restoration_model`: 马赛克修复模型(如 `"basicvsrpp-v1.2"`)
- `max_clip_length`: 单次修复片段的最大帧数，不是秒数
- `mosaic_detection_model`: 马赛克检测模型(如 `"v4-fast"`)
- `detect_face_mosaics`: 是否对人脸区域做马赛克检测
- `temporary_directory`: LADA 中间帧目录(留空用系统临时目录)
- `output_file_pattern`: 输出文件名模板,支持 `{orig_file_name}` 占位符,默认 `{orig_file_name}.restored.mp4`
- `mosaic_restoration_config_path`: 自定义修复模型配置文件的绝对路径

### 高级配置

- `max_concurrent_processes`: 最大并发处理数
- `move_processed_files`: 处理完成后是否移动原文件
- `processed_files_destination`: 处理文件移动目标目录
- `auto_start`: 启动 Web 或 Tk GUI 时是否自动启动监控
- `output_directory_name`: 自动生成的输出子目录名，默认 `Restored`
- `output_file_keywords`: 输出文件识别关键字列表（用于避免无限循环）
  - 默认值：`["_demosaic", ".restored"]`
  - 示例：如果LADA输出文件为`video.processed.mp4`，可设置为`[".processed"]`
- `temporary_file_extensions`: 下载/复制中的临时扩展名，命中后不会作为视频入队
- `file_ready_check_interval`: 文件大小稳定检测间隔秒数，默认 `2`
- `file_ready_retry_delay`: 文件未写完时的重试间隔秒数，默认 `10`
- `file_ready_max_retries`: 文件未写完时的最大重试次数，默认 `30`

### Uptime Kuma Push 监控

用于定时向Uptime Kuma的Push类型监控发送在线心跳。
心跳在监控启动后开始发送，停止监控后停止发送。

- `uptime_kuma.enabled`: 是否启用（默认 `false`）
- `uptime_kuma.push_url`: Uptime Kuma中创建Push监控后生成的URL（无需手动附加status/msg参数）
- `uptime_kuma.interval_seconds`: 心跳间隔秒数（默认 `60`）
- `uptime_kuma.status`: 发送状态（默认 `up`）
- `uptime_kuma.message`: 发送消息（默认 `LADA Agent online`）
  - 支持占位符：`{queue}`、`{processing}`、`{processed}`

## 使用方法

项目是常规的 `src/` 布局 Python 包,三个入口脚本由 `pyproject.toml` 的 `[project.scripts]` 暴露:

| 脚本 | 用途 |
|---|---|
| `lada-monitor` | 命令行监控守护 (前台 / 后台) |
| `lada-web` | Web 管理界面 (默认 `http://127.0.0.1:5000`) |
| `lada-gui` | Windows Tkinter 桌面管理 |

### 首次安装

```bash
uv sync
```

`uv sync` 会按 `pyproject.toml` 自动创建虚拟环境，并且只安装运行时依赖。

开发和测试工具使用独立依赖组：

```bash
uv sync --group dev
uv run --group dev pytest
uv run --group dev ruff check .
uv run --group dev ruff format --check .
```

### 三种入口

```bash
# 命令行监控 (前台, Ctrl+C 停止)
uv run lada-monitor

# 生成默认配置（已存在时拒绝覆盖；确认覆盖可加 --force）
uv run lada-monitor --init-config

# Web 管理界面
uv run lada-web --host 127.0.0.1 --port 5000

# Tk 桌面管理 (Windows)
uv run lada-gui
```

可选参数(仅 `lada-web`):
```bash
uv run lada-web --host 0.0.0.0 --port 5000 --debug
```

默认 Web 服务只监听 `127.0.0.1`。如果需要局域网访问,可显式设置 `--host 0.0.0.0`,并建议配合防火墙或反向代理认证;打开本机目录和文件夹选择器仅允许服务器本机浏览器调用。Web 静态资源已随包提供，局域网断网时也能加载。
非本机访问必须配置 `LADA_AGENT_ACCESS_TOKEN` 环境变量或 `config.json` 中的 `web_access_token`。

### Windows 一键脚本

仓库自带 `.bat` 包装,内部已切换到 `uv run`,无需手动激活虚拟环境:

```batch
scripts\start_monitor.bat     # 前台 lada-monitor
scripts\start_daemon.bat      # 后台 lada-monitor
scripts\start_web.bat         # lada-web (127.0.0.1:5000)
scripts\start_gui.bat         # lada-gui
```

Linux/macOS：

```bash
scripts/start_unix.sh          # 前台 lada-monitor
scripts/start_daemon_unix.sh   # 后台 lada-monitor，日志和 PID 写入 logs/
```

### 仓库边界

源码仓库只保留源码、脚本、文档和 `config/config_example.json`。真实配置、日志、运行状态、虚拟环境和打包产物已加入 `.gitignore`。发布包边界见 [`docs/RELEASE_ARTIFACTS.md`](docs/RELEASE_ARTIFACTS.md)。

## 拓展文档

- [`docs/usage.md`](docs/usage.md) —— 详细使用指南(三个入口、配置、日志路径、迁移)
- [`docs/README_WEB.md`](docs/README_WEB.md) —— Web 管理界面深读(四个页面、远程访问、令牌)
- [`docs/CHANGELOG.md`](docs/CHANGELOG.md) —— 版本变更日志
- [`docs/RELEASE_ARTIFACTS.md`](docs/RELEASE_ARTIFACTS.md) —— 源码 vs 本地状态 vs 发布产物的边界

## 程序说明

### 工作流程

1. **初始化**: 读取配置文件，设置日志，建立文件监控
2. **扫描现有文件**: 扫描监控目录中的现有视频文件
3. **实时监控**: 监听文件系统事件，检测新文件创建或移动
4. **文件验证**: 检查文件是否为支持的视频格式且已完成写入
5. **防重复检查**: 计算文件哈希，避免重复处理
6. **并发处理**: 使用信号量控制同时处理的文件数量
7. **LADA处理**: 调用lada-cli.exe处理视频文件
8. **结果记录**: 记录处理结果和文件信息

### 文件监控机制

- 使用`watchdog`库监听文件系统事件
- 支持递归监控子目录
- 自动过滤非视频文件
- 文件写入完成检测，避免处理不完整的文件

### 日志系统

程序会创建详细的日志记录，包括：
- 程序启动和停止信息
- 文件发现和处理进度
- LADA处理命令和结果
- 错误和异常信息

## 注意事项

1. **LADA环境**: 确保lada-cli.exe正确安装并可用
2. **磁盘空间**: 处理后的文件通常较大，确保输出目录有足够空间
3. **网络连接**: 如果使用在线模型，确保网络连接正常
4. **GPU资源**: 如果使用CUDA，确保GPU驱动正确安装
5. **文件权限**: 确保程序有读写相关目录的权限
6. **目录配置**: 程序智能处理输出目录在监控目录内的配置
   - ✅ 支持：监控目录 `C:\Downloads`，输出目录 `C:\Downloads\Restored`
   - ✅ 支持：监控目录 `C:\Downloads`，输出目录 `C:\Processed`
   - 🔒 防护：程序会自动识别并跳过自己的输出文件，避免无限循环
   - ⏳ 新文件仍在写入时会延迟重试，避免复制/下载未完成导致漏处理

## 故障排除

### 常见问题

1. **程序无法启动**
   - 检查 Python 版本(`pyproject.toml` 要求 `>=3.12`)
   - 运行 `uv sync` 重装依赖
   - 检查配置文件格式是否正确

2. **无法监控文件**
   - 检查监控目录是否存在和权限
   - 检查是否安装了watchdog库
   - 查看日志文件中的错误信息

3. **LADA处理失败**
   - 检查lada-cli.exe路径是否正确
   - 检查LADA模型文件是否存在
   - 查看控制台输出的具体错误信息

4. **输出文件被误处理**
   - 程序已内置防护机制，会自动跳过自己的输出文件
   - 如果发现输出文件被处理，检查日志中的调试信息

5. **性能问题**
   - 适当调整`max_concurrent_processes`参数
   - 根据硬件配置调整LADA处理参数
   - 监控系统资源使用情况

### 调试模式

如需更详细的调试信息，可以在config.json中设置：

```json
{
  "log_level": "DEBUG"
}
```

## 更新日志

按 [Keep a Changelog](https://keepachangelog.com/) 格式记录在 [`docs/CHANGELOG.md`](docs/CHANGELOG.md)。

当前版本:`0.2.0`(2026-07-18)。

## 贡献

欢迎提交问题和改进建议！

## 许可证

本项目遵循 [MIT 许可证](LICENSE)。第三方浏览器资源见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。
