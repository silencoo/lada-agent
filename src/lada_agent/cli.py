"""lada-agent command-line monitor entry point."""

import argparse

from lada_agent import __version__
from lada_agent.config_manager import initialize_config
from lada_agent.lada_monitor import main as run_monitor


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Monitor folders and restore new videos with LADA",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config", help="Path to config.json (default: auto-detect)")
    parser.add_argument(
        "--init-config",
        action="store_true",
        help="Create a complete starter config and exit",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing config when used with --init-config",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.force and not args.init_config:
        raise SystemExit("--force can only be used with --init-config")
    if args.init_config:
        try:
            path = initialize_config(args.config, overwrite=args.force)
        except FileExistsError as error:
            print(f"配置文件已存在: {error}")
            print("如需覆盖，请添加 --force。")
            return 1
        print(f"已创建配置文件: {path}")
        return 0

    return run_monitor(args.config)


if __name__ == "__main__":
    raise SystemExit(main())
