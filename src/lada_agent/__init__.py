"""LADA Agent package."""

__version__ = "0.2.0"

# lada_agent
#
# 自动监控指定目录中的新视频文件,并调用 LADA demosaic 工具进行处理。
# 三个入口:
#   - lada-monitor (cli.py)   命令行监控守护
#   - lada-web (web.py)       浏览器管理界面 (http://127.0.0.1:5000)
#   - lada-gui (gui.py)       Windows Tkinter 桌面管理
