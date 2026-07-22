# lada-agent 使用指南

`lada-agent` 是一个 `src/` 布局的标准 Python 包,由 `pyproject.toml` 暴露三个控制台脚本入口。读这篇文档大概 5 分钟,把"怎么启动、怎么调参、踩到坑怎么办"说清楚。

---

## 1. 准备

### 系统要求

- **Python 3.12+**(由 `pyproject.toml` 的 `requires-python` 锁定)
- **[uv](https://github.com/astral-sh/uv)** —— 包管理与虚拟环境一体化,跨平台
- **[LADA CLI](https://github.com/ladaapp/lada)** —— `lada-cli.exe` 单独发布,`lada-agent` 通过子进程调用它;**不**包含在本项目里

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
#   - lada_cli_path            lada-cli.exe 的绝对路径
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
| `lada-cli.exe` 子进程启动失败 | 路径错或模型权重未下载 | 用绝对路径,先单独跑一次 `lada-cli --list-devices` 验证 |
| Tk GUI 起不来 | 系统 Python 没装 `tk` | 装 `python.org` 的官方发行版,或 `apt install python3-tk` |

更多问题追踪见 [`README_WEB.md` 末尾的故障排查](README_WEB.md#故障排查) 和 [`AUDIT_2026-05-12.md`](AUDIT_2026-05-12.md) 里曾踩到的坑。
