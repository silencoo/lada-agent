# 发布产物边界

源码仓库只保留可维护源码、脚本、文档和示例配置。以下内容应作为本地运行状态或发布产物处理，不应进入源码提交：

- `.venv/`
- `cli/` 和 `cli/_internal/`
- `build/`
- `dist/`
- `logs/`
- `data/`
- `lada_agent/`
- `config/config.json`

`config/config_example.json` 是唯一应提交的配置文件。首次部署时复制为 `config/config.json`，再填写本机路径、访问令牌和 LADA CLI 路径。

通过 wheel 安装时也可以执行 `lada-monitor --init-config`，不需要从源码包复制示例。真实配置旁的 `.web_session_secret` 和 `.config.json.lada-monitor.lock` 都属于本机状态，不进入发布包；前者在迁移部署时应作为秘密文件备份，后者可以由程序自动重建。

Web UI 的 Socket.IO client、Remix Icon 字体和对应许可证属于可复现发布产物，保存在 `src/lada_agent/static/vendor/`，必须进入 wheel/EXE。详情见 [`../THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md)。

发布包建议通过 CI 或本机构建脚本生成，并上传到 release artifact。源码仓库不直接存放打包后的 `exe`、运行日志、处理记录或模型依赖目录。

发布前在 Windows 执行 `scripts/check.ps1`，在 Linux/macOS 执行 `scripts/check.sh`。两者会校验 lock、运行测试和 Ruff、检查 JavaScript 语法并构建 wheel/sdist。

0.3.0 的 Python 发布产物应为 `lada_agent-0.3.0-py3-none-any.whl` 和
`lada_agent-0.3.0.tar.gz`。CI 还会在无 `DISPLAY` 的 Ubuntu runner 上运行诊断/版本/命令
预览相关合同测试，并对全部 Unix 启动脚本执行 `sh -n`。
