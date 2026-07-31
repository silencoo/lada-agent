# lada-agent 使用指南

`lada-agent` 是一个 `src/` 布局的标准 Python 包,由 `pyproject.toml` 暴露三个控制台脚本入口。读这篇文档大概 5 分钟,把"怎么启动、怎么调参、踩到坑怎么办"说清楚。

---

## 1. 准备

### 系统要求

- **Python 3.12+**(由 `pyproject.toml` 的 `requires-python` 锁定)
- **[uv](https://github.com/astral-sh/uv)** —— 包管理与虚拟环境一体化,跨平台
- **[LADA CLI](https://github.com/ladaapp/lada)** —— `lada-cli` 单独安装,`lada-agent` 通过子进程调用它;**不**包含在本项目里

> 如果你从老版本(<0.1.0)升级,跳过"安装步骤",直接看 [§5 迁移指南](#5-迁移指南)。

### 安装

```bash
cd lada_agent

uv sync                     # 创建虚拟环境并安装依赖
uv sync --group dev         # 同时安装测试/开发工具(pytest、ruff)
uv run lada-monitor --init-config  # 生成 config/config.json
```

`uv sync` 完成后,三个入口都已经在 PATH 里,可以 `uv run lada-monitor`/`uv run lada-web`/`uv run lada-gui` 这样直接调用,**无需激活虚拟环境**。

---

## 2. 三个入口

| 入口 | 用途 | 平台 |
|---|---|---|
| `lada-monitor` | 命令行监控守护,前台阻塞运行,`Ctrl+C` 退出 | 全平台 |
| `lada-web` | 浏览器管理界面,绑定 `http://127.0.0.1:5000` | 全平台 |
| `lada-gui` | Tk 桌面管理面板 | Windows(其他平台可启动但不一定用得到) |

```bash
uv run lada-monitor                              # 命令行监控
uv run lada-web --host 127.0.0.1 --port 5000     # Web(默认参数)
uv run lada-gui                                  # GUI
```

Windows 一键脚本(在仓库根目录的 `scripts/` 下):

```cmd
scripts\start_monitor.bat    :: 前台 lada-monitor
scripts\start_daemon.bat     :: 后台 lada-monitor
scripts\start_web.bat        :: lada-web (127.0.0.1:5000)
scripts\start_gui.bat        :: lada-gui
```

### `lada-web` 命令行参数

```
--host HOST   监听地址 (默认 127.0.0.1;需要局域网访问用 0.0.0.0)
--port PORT   监听端口 (默认 5000)
--debug       启用 Flask 调试模式
--version     打印版本并退出
```

`lada-monitor` 还支持 `--config PATH`、`--init-config`、`--force` 和 `--version`。其中 `--force` 只用于确认覆盖已有配置。

Web/GUI 没有 `--config` 参数时，可以通过 `LADA_AGENT_CONFIG` 指向另一份配置；CLI 的显式 `--config` 优先级更高。

局域网访问和远程访问的相关安全约束,见 [`README_WEB.md`](README_WEB.md#远程访问与访问令牌) 的"远程访问与访问令牌"一节。

---

## 3. 日常任务

### 3.1 创建配置文件

仓库不提交真实 `config.json`(已在 `.gitignore`)。首次部署:

```bash
cp config/config_example.json config/config.json
# 用任意编辑器填写以下三项实际值:
#   - monitor_directories      要监控的视频目录列表
#   - output_directory         可选的统一输出目录；留空则在输入目录下使用 Restored
#   - lada_cli_path            lada-cli、lada-cli.exe 或宿主机 wrapper 的路径
```

也可以跨平台执行 `uv run lada-monitor --init-config`。该命令由代码默认值生成完整配置，因此通过 wheel 安装时也不依赖源码仓库里的示例文件。

完整字段与示例值见 [`config/config_example.json`](../config/config_example.json)，字段含义见 [`README.md`](../README.md#配置说明)。

### 3.2 启动并查看状态

```bash
uv run lada-monitor
```

启动时会输出:
- 配置加载结果(失败会立即退出)
- 工作目录
- 每个监视目录的注册状态
- "开始监控..." 提示

结束方式:`Ctrl+C`。

### 3.3 通过 Web 启动/停止

`uv run lada-web` 之后:

1. 浏览器打开 `http://127.0.0.1:5000`
2. Overview 页右上角的 **Start agent** / **Stop** 按钮控制 monitor 的启停
3. 停止时勾选 **Force stop** 图标会触发 `terminate_running`,直接结束 lada-cli 子进程

### 3.4 查看实时日志

- **Web**:Activity 页(终端面板),实时拉取 `lada_monitor.log` 末尾
- **GUI**:底部的 Logs 文本框,捕获程序所有 `stdout`/`stderr`
- **命令行情景**:`tail -f logs/lada_monitor.log`

### 3.5 运行测试

```bash
uv sync --group dev
uv run --group dev pytest                    # 全部
uv run --group dev pytest tests/test_core.py::LadaAgentCoreTests::test_unready_file_is_retried_and_then_queued   # 单条
```

### 3.6 Lint / 格式

```bash
uv run --group dev ruff check .
uv run --group dev ruff format --check .
```

### 3.7 Lada 参数与高级覆盖

Web 设置页的 **Detect Lada options** 和 Tk 面板的 **Detect Lada Options** 会执行当前
Lada CLI 的 `--version` 与全部 `--list-*` 信息命令，填充设备、模型、编码 preset 和
encoder 候选值。自定义模型路径和未被列出的值仍可手工输入。

推荐保留硬件感知默认值：

```json
{
  "lada_options": {
    "device": "auto",
    "fp16": null,
    "encoding_preset": "auto",
    "mosaic_restoration_model": "basicvsrpp-v1.2",
    "max_clip_length": 180,
    "mosaic_detection_model": "v4-fast",
    "detect_face_mosaics": false,
    "mp4_fast_start": false,
    "extra_cli_args": ""
  }
}
```

- `auto`/`null` 会省略对应 flag，让当前 Lada 构建按 CUDA、XPU、MPS 或 CPU 能力选择。
- FP16 选择 On/Off 时会明确传 `--fp16`/`--no-fp16`，不再依赖上游默认值。
- `detect_face_mosaics` 的真实含义是检测并排除脸部马赛克，避免把脸部区域送去修复。
- 输出模式必须包含 `{orig_file_name}` 和扩展名，防止多个输入写入同一路径。
- `encoder_options` 只有与 `encoder` 同时填写才有效；自定义 encoder 优先于 preset。

`extra_cli_args` 会作为参数数组追加到标准参数之后，因此可以覆盖设备、精度、模型或编码
设置，也能承载未来版本新增的处理参数，例如：

```json
{
  "extra_cli_args": "--device cpu --no-fp16"
}
```

出于任务一致性考虑，高级覆盖不能使用 `--input`、`--output`、`--output-file-pattern`、
`--help`、`--version` 或任何 `--list-*` 参数。这些信息类操作由能力探测接口提供。

### 3.8 无头诊断、命令预览与真实冒烟

以下命令都不需要桌面显示服务器：

```bash
# 检查 Lada 版本、设备、编码器、模型、目录权限和 FFmpeg
uv run lada-monitor --diagnose

# JSON 输出，适合部署脚本或健康检查
uv run lada-monitor --diagnose --json

# 显示最终 argv；高级覆盖会在末尾，命令不会执行
uv run lada-monitor --preview /srv/videos/sample.mp4

# 截取最多 3 秒临时视频，真实调用 Lada，再用 ffprobe 验证输出
uv run lada-monitor --smoke-test /srv/videos/sample.mp4 --smoke-seconds 3 \
  --smoke-max-width 640 --smoke-max-fps 5

# 显式保留冒烟产物
uv run lada-monitor --smoke-test /srv/videos/sample.mp4 \
  --smoke-max-width 0 \
  --smoke-max-fps 0 \
  --smoke-output /srv/videos/sample.smoke.restored.mp4
```

默认 `preflight_on_start=true`。每次启动 monitor 前会执行同一套关键检查；Lada、设备、
编码器、已选模型/预设或路径不可用时拒绝启动。FFmpeg/ffprobe 只用于短样本测试，缺失时
是警告，不会阻止正常监控。

Web 设置页的 **Runtime verification** 提供同样的诊断、最终 argv 预览和异步冒烟测试。
Web 冒烟输入必须位于已配置的监控目录，临时样本及默认测试输出会在验证结束后删除。

### 3.9 参数模板与单次任务

`lada_profiles` 中每个模板都是 `lada_options` 的部分覆盖。`directory_profiles` 的 key 是
目录，value 是模板名；多层路径同时匹配时使用最长路径。示例：

```json
{
  "lada_profiles": {
    "cpu-safe": {"device": "cpu", "fp16": false},
    "nvidia-hq": {"device": "cuda:0", "encoding_preset": "hevc-nvidia-gpu-hq"}
  },
  "directory_profiles": {
    "/srv/videos/cpu": "cpu-safe",
    "/srv/videos/hq": "nvidia-hq"
  }
}
```

Web 设置页还可以把一个现有文件按指定模板提交为单次任务。文件必须位于监控目录，且
monitor 已启动。最终优先级为：全局设置、目录模板、单次模板、高级 CLI 覆盖。

### 3.10 无显示器 Linux 服务

Agent 的 Web/monitor 路径不导入 Tkinter；没有 `DISPLAY` 或 Wayland 也能运行：

```bash
scripts/start_web_unix.sh

# 或仅运行 monitor
scripts/start_daemon_unix.sh
```

`packaging/systemd/` 包含 systemd user unit 示例，默认仓库位置为
`~/lada_agent`。复制到 `~/.config/systemd/user/` 后可启用其中一个：

```bash
systemctl --user daemon-reload
systemctl --user enable --now lada-agent-web.service
```

Web 配置 `auto_start=true` 时自身会启动 monitor，不要再同时启用
`lada-agent-monitor.service`。SSH 服务器推荐保持 `127.0.0.1` 并使用端口转发；监听
`0.0.0.0` 时必须设置 `LADA_AGENT_ACCESS_TOKEN` 并配合 HTTPS 反向代理。

Flatpak 可创建 wrapper，例如：

```sh
#!/usr/bin/env sh
exec flatpak run --filesystem=host --command=lada-cli io.github.ladaapp.lada "$@"
```

将 wrapper 设为可执行，并把 `lada_cli_path` 指向其绝对路径。Docker wrapper 同样必须
透传 `"$@"`，并将全部监控、输出、模型和临时路径 bind mount 到容器中的相同路径。

---

## 4. 部署为 Windows 服务

可选方案,任选其一:

- **NSSM** 把 `uv run lada-monitor` 包成服务(简单可靠)
- **Task Scheduler** 创建开机触发的任务(`uv run lada-monitor`)
- **PyInstaller 打包**:见 [`scripts/build_exe.bat`](../scripts/build_exe.bat),配置位于 [`packaging/LadaAgent.spec`](../packaging/LadaAgent.spec),产物在 `dist/LadaAgent.exe`,运行时仍需要 `config/config.json` 与 `cli/` 与它同级目录

---

## 5. 迁移指南

### 5.1 从 `uv init` 之前的扁平布局迁移

旧:`python src/lada_monitor.py`
新:`uv run lada-monitor`

如果你已经有 `requirements.txt` / `requirements_web.txt`,可以删掉 —— 依赖已搬到 `pyproject.toml` 的 `dependencies` 中。

Windows `.bat` 和 Linux/macOS `.sh` 启动脚本都已经统一使用 `uv sync --locked` / `uv run`。前台脚本退出时 monitor 一并停止，后台脚本会在 `logs/` 写输出与 PID。

### 5.2 加新依赖

```bash
uv add flask-cors                 # 运行时依赖
uv add --group dev pytest-mock    # 开发时依赖
```

不要手动编辑 `pyproject.toml` 的 `dependencies`,让 `uv add` 更新它并同步刷新 `uv.lock`。

### 5.3 加新控制台入口

往 `src/lada_agent/` 加一个模块 `xyz.py`,里头写:

```python
def main() -> None:
    ...

if __name__ == "__main__":
    main()
```

然后在 `pyproject.toml` 的 `[project.scripts]` 加一行:

```toml
lada-xyz = "lada_agent.xyz:main"
```

`uv sync` 之后,`uv run lada-xyz` 直接生效。

---

## 6. 故障排查

| 症状 | 原因 | 解法 |
|---|---|---|
| `ModuleNotFoundError: lada_agent` | 没装包或没在正确 venv | `uv sync`,或确认 IDE 用的解释器是 `.venv/Scripts/python.exe` |
| `uv sync` 卡在网络 | PyPI 不可达 | `UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple uv sync` |
| Web 启动时 `Address already in use` | 端口被占 | 换 `--port` |
| 远程访问 401 | 缺 token | 设 `LADA_AGENT_ACCESS_TOKEN` 或在 `config.json` 加 `web_access_token` |
| `lada-cli` 子进程启动失败 | 路径错、wrapper 权限错误或模型权重未下载 | 运行 `lada-monitor --diagnose` |
| Tk GUI 起不来 | 系统 Python 没装 `tk` | 装 `python.org` 的官方发行版,或 `apt install python3-tk` |

更多问题追踪见 [`README_WEB.md` 末尾的故障排查](README_WEB.md#故障排查) 和 [`AUDIT_2026-05-12.md`](AUDIT_2026-05-12.md) 里曾踩到的坑。
