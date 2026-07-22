# LADA Agent

LADA Agent 是一个面向本地与局域网环境的视频修复任务管理器。它可以持续监控目录，在发现新视频后调用 LADA CLI 执行 demosaic 修复，并通过命令行、桌面界面或 Web 仪表盘管理任务。

当前版本为 `0.2.0`，已验证兼容 LADA CLI `0.10.1`。关于上游版本适配范围，请参阅 [LADA 兼容性说明](docs/LADA_COMPATIBILITY.md)。

## 核心能力

| 能力 | 说明 |
| --- | --- |
| 实时监控 | 监听一个或多个目录，自动识别新建、移动或恢复扫描发现的视频文件 |
| 自动处理 | 文件写入稳定后调用 LADA CLI，并记录处理状态和结果 |
| 防重复与防循环 | 基于处理记录和输出关键字跳过已处理文件及程序自身生成的文件 |
| 并发控制 | 可限制同时运行的修复任务数量，避免过度占用 GPU、CPU 和磁盘 |
| 多端管理 | 提供命令行守护进程、Windows Tk 桌面界面和 LAN Web UI |
| Web 对比 | 支持状态筛选、历史清理、并排或滑动对比，以及全屏同步播放 |
| 运行保护 | 同一配置只允许一个 monitor 实例；远程 Web 访问支持 token 和持久化登录会话 |
| 跨平台脚本 | 提供 Windows 批处理脚本以及 Linux/macOS 启动脚本 |

## 系统要求

| 组件 | 要求 |
| --- | --- |
| Python | `3.12` 或更高版本 |
| 包管理器 | [uv](https://github.com/astral-sh/uv)，用于创建虚拟环境和锁定依赖 |
| LADA CLI | 已安装且可执行；可以加入 `PATH`，也可以在配置中填写完整路径 |
| 操作系统 | Windows 或 Linux；macOS 可使用 Unix 启动脚本，但 LADA CLI 兼容性取决于上游 |
| 硬件 | 取决于所选 LADA 模型和编码器；使用 CUDA 时需要兼容的 NVIDIA 驱动 |

## 快速开始

### 1. 获取源码并安装依赖

```bash
git clone https://github.com/silencoo/lada-agent.git
cd lada-agent
uv sync
```

开发和测试依赖不会默认安装。如需参与开发，请使用：

```bash
uv sync --group dev
```

### 2. 生成配置

```bash
uv run lada-monitor --init-config
```

该命令会根据 [config/config_example.json](config/config_example.json) 创建 `config/config.json`。如果配置已经存在，命令会拒绝覆盖；确认覆盖时可加 `--force`。

至少需要检查以下字段：

```json
{
  "monitor_directories": ["C:\\Downloads\\Videos"],
  "output_directory": "C:\\Downloads\\Restored",
  "lada_cli_path": "C:\\Tools\\lada-cli.exe",
  "max_concurrent_processes": 1,
  "lada_options": {
    "device": "cuda:0",
    "fp16": true,
    "encoding_preset": "hevc-nvidia-gpu-hq"
  }
}
```

真实配置、日志、处理记录、虚拟环境和构建产物均已加入 `.gitignore`，不会随正常提交进入仓库。

### 3. 启动服务

```bash
# 前台运行目录监控；按 Ctrl+C 停止
uv run lada-monitor

# 启动仅本机可访问的 Web 管理界面
uv run lada-web --host 127.0.0.1 --port 5000

# 启动 Windows 桌面管理界面
uv run lada-gui
```

安装后可用的入口如下：

| 命令 | 用途 |
| --- | --- |
| `lada-monitor` | 运行目录监控和任务处理 |
| `lada-web` | 启动 Web 管理界面 |
| `lada-gui` | 启动 Windows Tk 桌面界面 |

## Web 与远程访问

Web 服务默认只监听 `127.0.0.1:5000`。需要从局域网访问时，可以显式监听所有网卡：

```bash
uv run lada-web --host 0.0.0.0 --port 5000
```

非本机访问必须配置访问 token。推荐使用环境变量，避免把 token 写入配置文件：

```powershell
$env:LADA_AGENT_ACCESS_TOKEN = "replace-with-a-long-random-token"
uv run lada-web --host 0.0.0.0 --port 5000
```

登录会话使用持久化签名密钥。默认密钥保存在被忽略的 `config/.web_session_secret` 中；迁移部署时应将其作为秘密文件单独备份。局域网部署还应配合主机防火墙，跨网络访问建议使用带认证和 HTTPS 的反向代理。

完整的 Web 配置、安全模型和页面说明见 [Web UI 文档](docs/README_WEB.md)。

## 配置概览

### 文件与任务

| 字段 | 说明 |
| --- | --- |
| `monitor_directories` | 需要监控的目录列表 |
| `output_directory` | 统一输出目录；留空时使用输入目录下的 `output_directory_name` 子目录 |
| `supported_extensions` | 可入队的视频扩展名 |
| `scan_interval` | Watchdog 事件之外的恢复扫描间隔，单位为秒 |
| `max_concurrent_processes` | 同时运行的最大任务数 |
| `move_processed_files` | 处理成功后是否移动原文件 |
| `processed_files_destination` | 原文件移动目标目录 |
| `output_file_keywords` | 用于识别并跳过输出文件的关键字 |
| `temporary_file_extensions` | 下载或复制过程中不应入队的临时扩展名 |

### LADA 选项

常用字段包括 `device`、`fp16`、`encoding_preset`、`mosaic_restoration_model`、`mosaic_detection_model`、`max_clip_length` 和 `detect_face_mosaics`。

`encoder` 与 `encoder_options` 用于自定义 FFmpeg 编码参数，并优先于 `encoding_preset`。`max_clip_length` 的单位是帧，不是秒。Web API 仅接受 `config_manager.LADA_OPTION_SCHEMA` 中声明的字段，并会对数值执行边界检查。

所有字段、默认值和迁移说明见 [详细使用指南](docs/usage.md)。

## 启动脚本

Windows：

```batch
scripts\start_monitor.bat
scripts\start_daemon.bat
scripts\start_web.bat
scripts\start_gui.bat
```

Linux/macOS：

```bash
scripts/start_unix.sh
scripts/start_daemon_unix.sh
```

后台脚本的日志和 PID 文件会写入 `logs/`。

## 处理流程

1. 读取并验证配置，获取该配置对应的单实例锁。
2. 启动 Watchdog 监听并执行一次恢复扫描。
3. 等待候选文件大小稳定，过滤临时文件和程序输出文件。
4. 检查处理记录，避免重复入队。
5. 按并发上限调用 LADA CLI。
6. 保存处理结果、日志和历史记录。

## 开发与验证

```bash
uv sync --group dev
uv run --group dev pytest
uv run --group dev ruff check .
uv run --group dev ruff format --check .
```

源码使用标准 `src/` 布局，Python 包位于 `src/lada_agent/`。发布内容边界见 [发布产物说明](docs/RELEASE_ARTIFACTS.md)。

## 常见问题

| 问题 | 建议 |
| --- | --- |
| 程序无法启动 | 确认 Python 版本不低于 3.12，并重新运行 `uv sync` |
| 无法发现文件 | 检查监控目录是否存在、当前用户是否有读取权限，并查看 monitor 日志 |
| LADA 处理失败 | 检查 CLI 路径、模型文件、GPU 驱动和控制台错误输出 |
| 新文件迟迟未处理 | 文件可能仍在写入；检查 ready-check 参数和临时扩展名配置 |
| 输出文件再次入队 | 检查 `output_file_keywords` 是否包含实际输出文件使用的关键字 |
| Web 返回 401 | 配置 `LADA_AGENT_ACCESS_TOKEN`，并确认浏览器登录会话仍然有效 |

如需调试，可在 `config/config.json` 中将日志等级设为 `DEBUG`：

```json
{
  "log_level": "DEBUG"
}
```

## 文档

- [详细使用指南](docs/usage.md)
- [Web UI 文档](docs/README_WEB.md)
- [LADA 兼容性说明](docs/LADA_COMPATIBILITY.md)
- [版本变更日志](docs/CHANGELOG.md)
- [发布产物说明](docs/RELEASE_ARTIFACTS.md)

## 许可证

项目采用 [MIT License](LICENSE)。第三方浏览器资源的许可证信息见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
