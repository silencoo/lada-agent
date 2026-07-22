# LADA CLI 兼容性

`lada-agent` 调用的是独立的 LADA CLI，不把模型或可执行文件提交到本仓库。版本升级必须同时检查命令行参数、输出文件命名、编码兼容性和坏文件行为。

## 当前基线

| LADA CLI | lada-agent | 状态 | 说明 |
|---|---|---|---|
| 0.10.1 | 0.2.0 | 已验证基线 | 当前 `docs/upstream/lada-cli-help.txt` 的来源版本；主要开发环境为 NVIDIA CUDA。 |
| 0.11.0 | 0.2.0 | 待真实视频回归 | 上游当前升级目标；参数兼容性预期较高，但发布前仍需跑下面的检查。 |

记录日期：2026-07-18。上游发布页：<https://github.com/ladaapp/lada/releases>。

## 0.11.0 值得关注的变化

- Intel QSV 编码和 Intel Arc GPU 支持。
- GUI 字幕支持和 watch overlay。
- 损坏视频可在短缺口内重复上一帧，降低整段失败概率。
- 修复坏视频触发的潜在死锁。
- 改进 WMV/WMA、翻译和 SVT-AV1 日志行为。

在当前 NVIDIA 环境里，优先收益是坏视频恢复与死锁修复；Intel 编码功能只有在对应硬件上才需要接入设置页。

## 升级检查清单

1. 保存 `lada-cli --help` 和版本输出，更新 `docs/upstream/lada-cli-help.txt` 的版本与日期。
2. 用短样本验证默认 NVIDIA preset、自定义 encoder、音频和字幕保留。
3. 验证 `--mp4-fast-start` 输出是否符合浏览器 Range 播放预期。
4. 验证坏帧、损坏尾部、WMV/WMA 和无音频素材不会死锁。
5. 在 Web UI 中执行 Original/Restored 并排播放、Swipe compare、全屏、连续 seek 和缓冲恢复。
6. 确认输出文件模式仍与 `output_file_pattern` 和历史记录匹配。
7. 通过后再把 0.11.0 标记为“已验证基线”。

## 参数语义提醒

- `max_clip_length` 是最大帧数，不是秒。
- `mp4_fast_start` 是传给 LADA 的请求，不应在文档中保证所有编码器都会生成完全相同的 MP4 atom/fragment 布局。
- `encoder` / `encoder_options` 设置后优先于 `encoding_preset`。
