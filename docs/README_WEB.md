# lada-agent · Web 管理界面

> 适用版本：`lada-agent 0.3.0`。Web UI 由 Flask + Flask-SocketIO 提供，静态资源已经随 wheel/EXE 打包，不依赖公网 CDN。

Web 界面用于查看处理队列、控制内置 monitor、维护配置、检查历史视频并对比 Original 与 Restored。它不是独立媒体编辑器，也不会在清理历史记录时删除视频文件。

## 1. 启动

```bash
uv sync
uv run lada-monitor --init-config       # 首次部署
uv run lada-web                         # http://127.0.0.1:5000
```

局域网访问：

```bash
uv run lada-web --host 0.0.0.0 --port 5000
```

其他参数：

```text
--host HOST   监听地址，默认 127.0.0.1
--port PORT   监听端口，默认 5000
--debug       Flask 调试模式，仅用于开发
--version     打印版本并退出
```

Windows 也可以运行 `scripts\start_web.bat`。

无显示器 Linux 可以运行 `scripts/start_web_unix.sh`。Web/monitor 不需要 `DISPLAY`，
`packaging/systemd/lada-agent-web.service` 可作为 systemd user service 模板。

## 2. 页面与数据含义

### Overview

顶部状态条显示：

- **Agent status**：Web 内置 monitor 是否运行。
- **In progress**：正在处理的视频数量和源文件大小。
- **Waiting**：等待队列数量和大小。
- **History**：处理历史数量和历史源视频大小。

指标卡显示：

- **Pipeline remaining**：根据当前任务进度和观测速率估算的剩余时间。
- **Total video size**：历史源视频与当前队列源视频去重后的总大小；monitor 停止时仍会统计历史。
- **Processing load**：正在处理与等待处理的数据量。
- **Live throughput**：当前处理任务的估算总吞吐量。

`Start agent`、`Stop` 和 `Force stop` 只控制 Web 进程创建的 monitor。`Force stop` 会终止正在运行的 LADA CLI 子进程。

### Video review

左侧历史列表会在加载时检查 Original 和 Restored 的实际文件状态：

- **Ready**：两个文件都存在，可以完整比较。
- **Partial**：只剩一侧文件，可以单独播放存在的一侧。
- **Missing**：两侧文件都不存在，不会继续显示上一条历史的视频。

列表支持搜索、选择当前筛选结果、批量删除历史和 `Clean missing`。这些操作只重写 `processed_files.json`，**绝不删除 Original 或 Restored 媒体文件**。

查看器有两种模式：

1. **Side by side**：两侧各自保留原生视频控件，`Play both` 同步播放/暂停。
2. **Swipe compare**：Original 在底层、Restored 在裁剪层，拖动中间滑块检查差异。

Swipe compare 说明：

- Original / Restored 标签与实际方向一致。
- 只有滑块负责对比位置，视频区域仍可正常使用播放控件。
- 主视频控件负责进度、音量和播放；另一侧跟随时间与缓冲状态。
- `Fullscreen compare` 会让整个比较舞台进入全屏，滑块、标签、缓冲提示和控件仍可操作。
- 两侧未达到共同可播放位置时会显示 `Buffering both videos…`，避免一侧继续播放造成漂移。

浏览器不支持某个编码时，即使文件存在也会显示“cannot be played in this browser”。这和历史中的 Missing 状态不是同一问题。

### Activity

- **Processor**：`log_file` 指向的 LADA monitor 日志。
- **Web service**：`logs/web_access.log`。
- **Live**：控制定时刷新；内容可选择复制。

### Settings

设置包括 System、Runtime verification、Restoration、Detection、Encoding、Paths、Uptime Kuma、Advanced CLI override 和 Task parameter templates。保存时后端会通过 `sanitize_config_update` 做字段白名单、类型和边界检查。

Runtime verification 可以运行环境诊断、预览最终 argv，并以监控目录中的现有视频执行异步短样本冒烟。参数模板可以按目录自动生效，也可以用于手工提交的一次任务。`preflight_on_start` 默认开启，明确不可用的设备、模型、preset、encoder 或路径会阻止 monitor 启动。

注意：`max_clip_length` 的单位是**帧**，不是秒。`mp4_fast_start` 只是把对应选项传给 LADA，最终 MP4 布局仍受编码器和容器实现影响。

## 3. 登录、令牌与会话

本机回环访问不要求 token。非本机请求必须配置以下任一项：

```powershell
$env:LADA_AGENT_ACCESS_TOKEN = "replace-with-a-long-random-token"
uv run lada-web --host 0.0.0.0
```

或在 `config/config.json` 中设置：

```json
{
  "web_access_token": "replace-with-a-long-random-token"
}
```

API 客户端支持：

| 方式 | 示例 |
|---|---|
| Bearer | `Authorization: Bearer TOKEN` |
| 自定义头 | `X-LADA-Token: TOKEN` |
| 登录表单 | `token=TOKEN` |

URL 查询参数不会被当作 token，避免令牌进入浏览器历史和访问日志。跨站的 POST/DELETE 请求会被拒绝。

登录成功后使用 HttpOnly、SameSite=Lax 的签名 session cookie。签名密钥默认持久化到 `config/.web_session_secret`，所以正常重启不会清除登录状态。部署迁移时应和配置一起保留该文件，也可以显式设置：

```powershell
$env:LADA_AGENT_SECRET_KEY = "a-long-random-session-signing-secret"
# 或
$env:LADA_AGENT_SECRET_KEY_FILE = "D:\Secrets\lada-session-key"
```

如果删除 `.web_session_secret`、更换 `LADA_AGENT_SECRET_KEY` 或浏览器 cookie，所有现有登录会话都会失效。

### 远程用户能做什么

通过 token 登录的远程用户可以查看视频、日志与状态，启动/停止 Web monitor，修改配置，以及删除历史记录。因此 token 是管理凭据，不是只读密码。

以下宿主操作始终只允许回环地址并要求 Host 也是 loopback：

- 调用系统文件夹选择器。
- 在服务器操作系统中打开目录。
- 打开最近的 Restored 输出目录。

局域网 HTTP 会明文传输 token。可信家庭 LAN 之外应使用 HTTPS 反向代理或 VPN，不要直接把 `0.0.0.0:5000` 暴露到公网。

## 4. 离线资源

默认使用仓库内固定版本：

- Socket.IO client 4.7.5
- Remix Icon 3.5.0
- 系统字体回退

许可证见 [`../THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md)。如有特殊部署需求，可以在 `config.json` 中设置 `web_socketio_client_url` 覆盖默认客户端脚本，但通常不需要。

## 5. 单实例规则

CLI、Web 和 GUI 对同一份 `config.json` 使用同目录下的 `.config.json.lada-monitor.lock` 做 OS 级互斥。第二个 monitor 会拒绝启动，避免同时扫描和处理同一批文件。

锁文件本身可以长期存在；真正的所有权由操作系统文件锁决定，不通过“文件是否存在”判断，因此进程崩溃后不会留下永久死锁。

不同配置文件会使用不同锁。如果它们监控相同目录，仍然可能产生竞争，部署时应避免这种配置。

## 6. 故障排查

### 登录后重启又退出

检查 `config/.web_session_secret` 是否可写、是否被清理脚本删除，以及 `LADA_AGENT_SECRET_KEY` 是否每次启动都改变。

### 页面能打开但状态不更新

确认 `static/vendor/socket.io/socket.io.min.js` 可以返回 200；即使 Socket.IO 连接失败，页面仍会使用定时 REST 刷新，但实时通知会延迟。

### 视频存在但无法播放

先在 `/api/media/<hash>/<kind>` 响应中确认 200/206 和 `Content-Range`，再检查浏览器是否支持视频编码。服务器支持 HTTP Range 和一小时私有缓存。

### Compare 卡顿或两侧漂移

等待缓冲提示消失后播放；频繁跨大范围 seek 时浏览器需要重新请求两侧媒体 Range。网络吞吐不足或编码关键帧间隔过大也会增加等待时间。

### Start agent 提示已有 monitor

检查是否已经运行 `lada-monitor`、另一个 `lada-web` 或桌面 GUI。不要删除锁文件来绕过保护；先停止真正占用配置的进程。

## 7. 代码位置

- Web 入口：`src/lada_agent/web.py`
- Flask/Socket.IO/API：`src/lada_agent/web_app.py`
- HTML：`src/lada_agent/templates/index.html`、`login.html`
- 交互：`src/lada_agent/static/app.js`
- 样式：`src/lada_agent/static/app.css`、`theme.css`
- 本地资源：`src/lada_agent/static/vendor/`
- 后端与 UI 合同测试：`tests/test_core.py`、`tests/test_release_contracts.py`
