"""lada-agent command-line monitor entry point."""

import argparse
import json
import os

from lada_agent import __version__
from lada_agent.config_manager import initialize_config, load_config, resolve_runtime_paths
from lada_agent.diagnostics import (
    diagnose_environment,
    merge_lada_options_for_path,
    render_diagnostic_report,
    run_lada_smoke_test,
)
from lada_agent.lada_client import build_lada_command_preview
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
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument(
        "--diagnose",
        action="store_true",
        help="Check headless runtime, Lada, devices, encoders, and paths, then exit",
    )
    actions.add_argument(
        "--preview",
        metavar="INPUT",
        help="Print the final Lada argv for an input without running it",
    )
    actions.add_argument(
        "--smoke-test",
        metavar="INPUT",
        help="Create and process a short sample from an existing video, then exit",
    )
    parser.add_argument("--profile", help="Named Lada parameter template for preview/smoke test")
    parser.add_argument("--preview-output", help="Output path shown by --preview")
    parser.add_argument("--smoke-output", help="Keep smoke-test output at this path")
    parser.add_argument(
        "--smoke-seconds", type=int, default=3, help="Sample duration from 1 to 30 seconds"
    )
    parser.add_argument(
        "--smoke-max-width",
        type=int,
        default=640,
        help="Downscale the sample to this width; use 0 to keep source resolution",
    )
    parser.add_argument(
        "--smoke-max-fps",
        type=int,
        default=5,
        help="Limit sample frame rate; use 0 to keep source frame rate",
    )
    parser.add_argument(
        "--smoke-timeout", type=int, default=1800, help="Lada smoke-test timeout in seconds"
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
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

    if args.diagnose or args.preview or args.smoke_test:
        config, config_path = load_config(args.config)
        if not config:
            print(f"无法读取配置文件: {config_path}")
            return 2
        resolve_runtime_paths(config, base_dir=os.path.dirname(config_path))

        if args.diagnose:
            report = diagnose_environment(config)
            print(
                json.dumps(report, ensure_ascii=False, indent=2)
                if args.json
                else render_diagnostic_report(report)
            )
            return 0 if report["status"] != "fail" else 2

        if args.preview:
            options, _profiles = merge_lada_options_for_path(config, args.preview, args.profile)
            output = args.preview_output or os.path.join(
                os.path.dirname(os.path.abspath(args.preview)),
                f"{os.path.splitext(os.path.basename(args.preview))[0]}.restored.mp4",
            )
            preview = build_lada_command_preview(
                str(config.get("lada_cli_path") or "lada-cli"),
                os.path.abspath(args.preview),
                os.path.abspath(output),
                options,
            )
            print(
                json.dumps(preview, ensure_ascii=False, indent=2)
                if args.json
                else preview["command"]
            )
            return 0

        try:
            result = run_lada_smoke_test(
                config,
                args.smoke_test,
                profile_name=args.profile,
                seconds=args.smoke_seconds,
                max_width=args.smoke_max_width,
                max_fps=args.smoke_max_fps,
                timeout=args.smoke_timeout,
                keep_output=bool(args.smoke_output),
                output_path=args.smoke_output,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"冒烟测试失败: {exc}")
            return 2
        print(
            json.dumps(result, ensure_ascii=False, indent=2)
            if args.json
            else (
                f"冒烟测试{'通过' if result['success'] else '失败'}；"
                f"耗时 {result['duration_seconds']} 秒；输出 {result['output_size']} bytes"
            )
        )
        return 0 if result["success"] else 1

    return run_monitor(args.config)


if __name__ == "__main__":
    raise SystemExit(main())
