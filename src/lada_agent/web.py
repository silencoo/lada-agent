"""lada-agent Web management interface entry point."""

import argparse

from lada_agent import __version__
from lada_agent.web_app import run_web_app


def main() -> None:
    parser = argparse.ArgumentParser(
        description="LADA 监控程序 Web 管理界面",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--host", default="127.0.0.1", help="Web 服务器主机地址 (默认: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=5000, help="Web 服务器端口 (默认: 5000)")
    parser.add_argument("--debug", action="store_true", help="启用调试模式")
    args = parser.parse_args()

    run_web_app(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
