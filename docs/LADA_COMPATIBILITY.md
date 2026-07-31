# LADA CLI 兼容性

`lada-agent` 调用的是独立的 LADA CLI，不把模型或可执行文件提交到本仓库。版本升级必须同时检查命令行参数、输出文件命名、编码兼容性和坏文件行为。

## 当前基线

| LADA CLI | lada-agent | 状态 | 说明 |
|---|---|---|---|
| 0.10.1 | 0.3.0 | 旧版兼容模式 | 当前 `docs/upstream/lada-cli-help.txt` 的来源版本。 |
| 0.11.0 | 0.3.0 | 源码参数契约已验证；可执行本机冒烟 | 全部处理参数、信息类 list 命令、Intel XPU/QSV 动态默认值和负向布尔 flag 已对照源码。 |
| 0.11.1-dev | 0.3.0 | CLI 表面兼容；运行时警告 | 当前 main 新增 MPS/VideoToolbox；处理参数名未变化，尚非正式发布。 |

记录日期：2026-07-31。上游发布页：<https://github.com/ladaapp/lada/releases>。

## 本机运行时回归

本机仓库内实际捆绑的是 Lada 0.10.1，不是 0.11.0。2026-07-31 在 Windows、
NVIDIA GeForce RTX 4080 SUPER 上得到以下结果；这些结果只评价现有二进制，不应外推为
0.11.0 的硬件结论。

| 路径 | 样本 | 时限 | 结果 |
|---|---|---:|---|
| 能力探测 | `--list-devices` / `--list-encoding-presets` | 30 秒 | 通过；发现 `cpu`、`cuda:0` 和 CPU/NVIDIA presets。 |
| CUDA 完整帧率 | 实际视频 1 秒，1920×1080，约 30 帧 | 1800 秒 | 超时；GPU 持续有利用率和约 3.8–4.1 GB 显存占用，但没有生成输出。 |
| CUDA 最小样本 | 同源视频 1 秒，320×180，2 fps，共 2 帧 | 600 秒 | 超时；没有生成输出。 |
| CPU 最小样本 | 合成视频 1 秒，160×90，1 fps，共 1 帧 | 300 秒 | 超时；进程持续消耗 CPU，但没有生成输出。 |
| macOS / MPS | 未执行 | — | 按当前测试范围跳过。 |

所有超时均由 Agent 终止子进程、清理临时目录并返回失败。Lada stderr 同时报告无法写入
Ultralytics 用户配置文件；目前没有证据证明该权限告警就是超时根因。要把 0.11.0 标记为
真实硬件通过，仍需下载官方 NVIDIA Windows 两段发布包（合计约 2.50 GB）后重跑。

## 0.11.0 值得关注的变化

- Intel QSV 编码和 Intel Arc GPU 支持。
- GUI 字幕支持和 watch overlay。
- 损坏视频可在短缺口内重复上一帧，降低整段失败概率。
- 修复坏视频触发的潜在死锁。
- 改进 WMV/WMA、翻译和 SVT-AV1 日志行为。

Agent 默认不再强制 `cuda:0` 和 NVIDIA preset。`device=auto`、`encoding_preset=auto`、
`fp16=null` 会保留上游按硬件选择 CUDA/XPU/CPU、NVENC/QSV/CPU 编码和 FP16 的行为。
设置页可直接读取实际 Lada 包提供的值，不在 Agent 内维护容易过期的枚举。

## 参数覆盖规则

- Agent 管理单文件 `--input`、`--output` 和安全的 `output_file_pattern` 展开。
- 标准设置会生成 Lada 0.11 的全部处理参数，并显式生成布尔正/负 flag。
- `extra_cli_args` 最后追加，可覆盖推理、检测、模型和编码参数，以兼容未来新增选项。
- 任务边界、帮助、版本和 `--list-*` 参数禁止在覆盖层使用；Web 能力接口负责这些操作。
- 自定义模型字段允许最长 1000 字符，避免 Windows 深层路径被 Web API 截断。

## 运行时状态定义

- `source-verified`：版本为 0.11.0，源码参数契约已验证；不等于用户硬件已验证。
- `compatible-unverified`：同属 0.11 系列但不是已发布基线，需要真实短视频冒烟。
- `legacy-compatible`：0.10 系列核心参数可用，但缺少 0.11 的硬件/修复改进。
- `future-unverified`：版本高于当前基线，启动前显示警告并要求检查 argv/冒烟结果。
- `unsupported`：早于 0.10，启动前诊断失败。

`lada-monitor --diagnose` 和 Web Runtime verification 会读取真实版本及 list 命令，检查已选
设备、模型、preset 和 encoder。默认启动前检查会阻止明确不可用的配置。真实冒烟测试
使用 FFmpeg 截取短样本，默认限制为 640px 和 5fps，执行实际 Lada CLI，再用 ffprobe
验证输出容器；`--smoke-max-width 0 --smoke-max-fps 0` 可保留源分辨率和帧率。结果只代表
当前主机、当前 Lada 构建、当前模板和样本。

## 升级检查清单

1. 保存 `lada-cli --help` 和版本输出，更新 `docs/upstream/lada-cli-help.txt` 的版本与日期。
2. 用短样本验证默认 NVIDIA preset、自定义 encoder、音频和字幕保留。
3. 验证 `--mp4-fast-start` 输出是否符合浏览器 Range 播放预期。
4. 验证坏帧、损坏尾部、WMV/WMA 和无音频素材不会死锁。
5. 在 Web UI 中执行 Original/Restored 并排播放、Swipe compare、全屏、连续 seek 和缓冲恢复。
6. 确认输出文件模式仍与 `output_file_pattern` 和历史记录匹配。
7. 通过后再把 0.11.0 标记为“真实视频已验证基线”。

## 参数语义提醒

- `max_clip_length` 是最大帧数，不是秒。
- `mp4_fast_start` 是传给 LADA 的请求，不应在文档中保证所有编码器都会生成完全相同的 MP4 atom/fragment 布局。
- `encoder` / `encoder_options` 设置后优先于 `encoding_preset`。
