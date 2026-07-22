# LADA Agent · 文档目录

`lada-agent` 是一个监控本地目录新视频文件,自动调用 [LADA](https://github.com/ladaapp/lada) demosaic 工具做修复的守护服务。本目录是除根 `README.md` 之外的延伸文档。

## 用户文档

| 文件 | 用途 |
|---|---|
| [`README.md`](../README.md) | 项目主页(快速开始、功能列表、最新变更指引) |
| [`usage.md`](usage.md) | 当前推荐使用方式:`uv run` 三个入口、常见任务、迁移提示 |
| [`README_WEB.md`](README_WEB.md) | Web 管理界面深度说明:四个页面、远程访问、访问令牌、设置项 |
| [`LADA_COMPATIBILITY.md`](LADA_COMPATIBILITY.md) | 已验证/目标 LADA CLI 版本、升级收益与真实视频回归清单 |
| [`upstream/lada-cli-help.txt`](upstream/lada-cli-help.txt) | 上游 `lada-cli.exe --help` 抓取的输出(非本项目产出) |

## 开发者/运维文档

| 文件 | 用途 |
|---|---|
| [`CHANGELOG.md`](CHANGELOG.md) | 按 [Keep a Changelog](https://keepachangelog.com/) 格式记录每个版本的可见变更 |
| [`AUDIT_2026-05-12.md`](AUDIT_2026-05-12.md) | 2026-05-12 安全与稳定性审计结果(历史快照,**已冻结**) |
| [`RELEASE_ARTIFACTS.md`](RELEASE_ARTIFACTS.md) | 仓库边界:源码 vs 本地运行状态 vs 发布产物的区分 |
| [`../THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md) | 离线 Web UI 所带第三方浏览器资源及许可证 |

## 文档维护约定

- **架构或代码入口变化** → 同步更新 `usage.md`,必要时更新 `README_WEB.md`。
- **可见变更**(新依赖、新脚本、破坏性配置变更) → 在 `CHANGELOG.md` 的 `[Unreleased]` 加条目;出新版时挪到带日期的版本块。
- **准备提交/发布** → 运行 `scripts/check.ps1`（Windows）或 `scripts/check.sh`（Linux/macOS）。
- **一次性深度审计** → 新建 `docs/AUDIT_YYYY-MM-DD.md`,并在 `CHANGELOG.md` 备注索引。
- **不要把运行日志、处理记录、个人配置写进** `docs/`。

## 一句话速记

```bash
uv sync                              # 安装依赖
uv run lada-monitor                  # 命令行监控
uv run lada-web                      # Web 界面 (http://127.0.0.1:5000)
uv run lada-gui                      # Tk 桌面面板 (Windows)
```
