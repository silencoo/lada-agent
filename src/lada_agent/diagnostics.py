"""Headless environment diagnostics and explicit Lada smoke tests."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from lada_agent.lada_client import (
    build_lada_command,
    build_lada_command_preview,
    discover_lada_capabilities,
    is_auto,
    validate_lada_options,
)


def resolve_lada_executable(value: Any) -> str | None:
    command = str(value or "lada-cli").strip() or "lada-cli"
    expanded = os.path.expandvars(os.path.expanduser(command))
    if os.path.isabs(expanded) or any(separator in expanded for separator in ("/", "\\")):
        return os.path.abspath(expanded) if os.path.isfile(expanded) else None
    return shutil.which(expanded)


def merge_lada_profile(config: dict[str, Any], profile_name: str | None = None) -> dict[str, Any]:
    options = dict(config.get("lada_options") or {})
    if not profile_name:
        return options
    profiles = config.get("lada_profiles") or {}
    if profile_name not in profiles:
        raise ValueError(f"不存在 Lada 参数模板: {profile_name}")
    overrides = profiles[profile_name]
    if not isinstance(overrides, dict):
        raise ValueError(f"Lada 参数模板 {profile_name} 格式无效")
    options.update(overrides)
    errors = validate_lada_options(options)
    if errors:
        raise ValueError("；".join(errors))
    return options


def merge_lada_options_for_path(
    config: dict[str, Any], input_path: str, profile_name: str | None = None
) -> tuple[dict[str, Any], list[str]]:
    """Merge global, longest directory match, then a one-off task profile."""
    target = os.path.normcase(os.path.abspath(input_path))
    matches: list[tuple[int, str]] = []
    for directory, name in (config.get("directory_profiles") or {}).items():
        root = os.path.normcase(os.path.abspath(str(directory)))
        try:
            if os.path.commonpath([target, root]) == root:
                matches.append((len(root), str(name)))
        except (OSError, ValueError):
            continue
    directory_profile = max(matches, default=(0, ""))[1] or None
    options = dict(config.get("lada_options") or {})
    applied: list[str] = []
    profiles = config.get("lada_profiles") or {}
    for name in (directory_profile, profile_name):
        if not name or name in applied:
            continue
        overrides = profiles.get(name)
        if not isinstance(overrides, dict):
            raise ValueError(f"Lada 参数模板不存在或无效: {name}")
        options.update(overrides)
        applied.append(name)
    errors = validate_lada_options(options)
    if errors:
        raise ValueError("；".join(errors))
    return options, applied


def _check(
    identifier: str, label: str, status: str, message: str, **details: Any
) -> dict[str, Any]:
    return {
        "id": identifier,
        "label": label,
        "status": status,
        "message": message,
        "details": details,
    }


def diagnose_environment(
    config: dict[str, Any], *, capability_timeout: float = 20
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    headless = not bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    checks.append(
        _check(
            "headless",
            "显示环境",
            "pass",
            "未检测到显示服务器；monitor、Web 和 Lada CLI 可无头运行。"
            if headless and os.name != "nt"
            else "当前环境有图形会话或不需要 Unix DISPLAY。",
            headless=headless,
            display=os.environ.get("DISPLAY", ""),
            wayland_display=os.environ.get("WAYLAND_DISPLAY", ""),
        )
    )

    configured_cli = str(config.get("lada_cli_path") or "lada-cli").strip() or "lada-cli"
    resolved_cli = resolve_lada_executable(configured_cli)
    if resolved_cli:
        executable = os.name == "nt" or os.access(resolved_cli, os.X_OK)
        checks.append(
            _check(
                "lada-cli",
                "Lada CLI",
                "pass" if executable else "fail",
                f"已解析到 {resolved_cli}" if executable else "文件存在但当前用户不可执行。",
                configured=configured_cli,
                resolved=resolved_cli,
            )
        )
    else:
        checks.append(
            _check(
                "lada-cli",
                "Lada CLI",
                "fail",
                "找不到 Lada CLI。Linux Flatpak/Docker 用户可配置一个宿主机 wrapper 脚本。",
                configured=configured_cli,
            )
        )

    capabilities: dict[str, Any] | None = None
    if resolved_cli:
        capabilities = discover_lada_capabilities(resolved_cli, timeout=capability_timeout)
        compatibility = capabilities.get("compatibility") or {}
        compatibility_status = compatibility.get("status")
        status = "pass" if capabilities.get("ok") else "fail"
        if compatibility_status in {
            "compatible-unverified",
            "legacy-compatible",
            "future-unverified",
        }:
            status = "warn" if capabilities.get("ok") else "fail"
        if compatibility_status == "unsupported":
            status = "fail"
        checks.append(
            _check(
                "lada-version",
                "Lada 版本兼容性",
                status,
                compatibility.get("message")
                or capabilities.get("version_error")
                or "能力探测未返回版本信息。",
                version=compatibility.get("version", ""),
                compatibility=compatibility_status or "unknown",
            )
        )
        device_result = (capabilities.get("capabilities") or {}).get("devices") or {}
        checks.append(
            _check(
                "compute-device",
                "计算设备",
                "pass" if device_result.get("ok") and device_result.get("values") else "fail",
                "可用设备: " + ", ".join(device_result.get("values") or [])
                if device_result.get("values")
                else device_result.get("error") or "Lada 未报告可用计算设备。",
                values=device_result.get("values") or [],
            )
        )
        encoder_result = (capabilities.get("capabilities") or {}).get("encoders") or {}
        checks.append(
            _check(
                "encoders",
                "视频编码器",
                "pass" if encoder_result.get("ok") and encoder_result.get("values") else "fail",
                f"Lada 报告 {len(encoder_result.get('values') or [])} 个编码器。"
                if encoder_result.get("values")
                else encoder_result.get("error") or "Lada 未报告可用编码器。",
                values=encoder_result.get("values") or [],
            )
        )

        available = capabilities.get("capabilities") or {}
        option_sets: list[tuple[str, dict[str, Any]]] = [
            ("global", dict(config.get("lada_options") or {}))
        ]
        option_problems: list[str] = []
        for profile_name in config.get("lada_profiles") or {}:
            try:
                option_sets.append((profile_name, merge_lada_profile(config, profile_name)))
            except ValueError as exc:
                option_problems.append(str(exc))
        for label, options in option_sets:
            comparisons = (
                ("device", "devices"),
                ("mosaic_restoration_model", "restoration_models"),
                ("mosaic_detection_model", "detection_models"),
            )
            for option_name, capability_name in comparisons:
                selected = options.get(option_name)
                if is_auto(selected) or os.path.isfile(str(selected)):
                    continue
                values = (available.get(capability_name) or {}).get("values") or []
                if selected not in values:
                    option_problems.append(f"{label}.{option_name}={selected}")
            encoder = str(options.get("encoder") or "").strip()
            preset = options.get("encoding_preset")
            if encoder:
                values = (available.get("encoders") or {}).get("values") or []
                if encoder not in values:
                    option_problems.append(f"{label}.encoder={encoder}")
            elif not is_auto(preset):
                values = (available.get("encoding_presets") or {}).get("values") or []
                if preset not in values:
                    option_problems.append(f"{label}.encoding_preset={preset}")
            restoration_config = str(options.get("mosaic_restoration_config_path") or "").strip()
            if restoration_config and not os.path.isfile(restoration_config):
                option_problems.append(
                    f"{label}.mosaic_restoration_config_path={restoration_config}"
                )
        checks.append(
            _check(
                "selected-options",
                "已选参数可用性",
                "fail" if option_problems else "pass",
                "以下选项未由当前 Lada 报告: " + ", ".join(option_problems)
                if option_problems
                else "全局设置和参数模板均可由当前 Lada 解析。",
                problems=option_problems,
            )
        )
        raw_outputs = [capabilities.get("version") or ""]
        raw_outputs.extend(str(result.get("output") or "") for result in available.values())
        runtime_warnings = sorted(
            {
                line.strip()
                for output in raw_outputs
                for line in output.splitlines()
                if "permission denied" in line.lower() or line.lstrip().startswith("ERROR ")
            }
        )
        if runtime_warnings:
            checks.append(
                _check(
                    "lada-runtime-warnings",
                    "Lada 运行时警告",
                    "warn",
                    "Lada 可运行，但报告了配置/缓存写入警告。请检查服务用户的 HOME 权限。",
                    warnings=runtime_warnings[:10],
                )
            )

    directories = config.get("monitor_directories") or []
    if not directories:
        checks.append(_check("monitor-directories", "监控目录", "fail", "尚未配置监控目录。"))
    else:
        missing = [str(path) for path in directories if not os.path.isdir(path)]
        unreadable = [
            str(path)
            for path in directories
            if os.path.isdir(path) and not os.access(path, os.R_OK | os.X_OK)
        ]
        status = "fail" if missing or unreadable else "pass"
        message = (
            f"{len(directories)} 个监控目录均可读取。"
            if status == "pass"
            else f"缺失 {len(missing)} 个，不可读取 {len(unreadable)} 个。"
        )
        checks.append(
            _check(
                "monitor-directories",
                "监控目录",
                status,
                message,
                missing=missing,
                unreadable=unreadable,
            )
        )

    output_directory = str(config.get("output_directory") or "").strip()
    output_targets = (
        [output_directory]
        if output_directory
        else [
            os.path.join(path, str(config.get("output_directory_name") or "Restored"))
            for path in directories
        ]
    )
    unwritable: list[str] = []
    for target in output_targets:
        existing = target
        while existing and not os.path.exists(existing):
            parent = os.path.dirname(existing)
            if parent == existing:
                break
            existing = parent
        if (
            not existing
            or not os.path.isdir(existing)
            or not os.access(existing, os.W_OK | os.X_OK)
        ):
            unwritable.append(target)
    checks.append(
        _check(
            "output-directory",
            "输出目录",
            "pass" if output_targets and not unwritable else "fail",
            "输出目录或其最近父目录可写。"
            if output_targets and not unwritable
            else "部分输出路径不可创建。",
            targets=output_targets,
            unwritable=unwritable,
        )
    )

    temporary_directory = str(
        (config.get("lada_options") or {}).get("temporary_directory") or tempfile.gettempdir()
    )
    checks.append(
        _check(
            "temporary-directory",
            "临时目录",
            "pass"
            if os.path.isdir(temporary_directory)
            and os.access(temporary_directory, os.W_OK | os.X_OK)
            else "fail",
            f"临时目录可写: {temporary_directory}"
            if os.path.isdir(temporary_directory)
            and os.access(temporary_directory, os.W_OK | os.X_OK)
            else f"临时目录不存在或不可写: {temporary_directory}",
            path=temporary_directory,
        )
    )

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    checks.append(
        _check(
            "ffmpeg",
            "FFmpeg 冒烟工具",
            "pass" if ffmpeg and ffprobe else "warn",
            "ffmpeg/ffprobe 可用于截取并验证短样本。"
            if ffmpeg and ffprobe
            else "缺少 ffmpeg 或 ffprobe；正常监控不受影响，但短样本冒烟测试不可用。",
            ffmpeg=ffmpeg or "",
            ffprobe=ffprobe or "",
        )
    )

    statuses = [check["status"] for check in checks]
    overall = "fail" if "fail" in statuses else "warn" if "warn" in statuses else "pass"
    return {
        "status": overall,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "headless": headless,
        "checks": checks,
        "capabilities": capabilities,
        "checked_at": datetime.now().isoformat(),
    }


def render_diagnostic_report(report: dict[str, Any]) -> str:
    symbols = {"pass": "PASS", "warn": "WARN", "fail": "FAIL"}
    lines = [
        f"LADA Agent environment: {report.get('status', 'unknown').upper()}",
        f"Platform: {report.get('platform', '')}",
    ]
    for check in report.get("checks") or []:
        symbol = symbols.get(check.get("status"), "INFO")
        lines.append(f"[{symbol}] {check.get('label')}: {check.get('message')}")
    return "\n".join(lines)


def _make_short_sample(
    ffmpeg: str,
    input_path: str,
    sample_path: str,
    seconds: int,
    max_width: int,
    max_fps: int,
) -> None:
    filters: list[str] = []
    if max_width > 0:
        # Keep the smoke test cheap enough for CPU-only/headless health checks. The
        # expression avoids upscaling already-small inputs and preserves an even
        # height for encoders that require 4:2:0-compatible dimensions.
        filters.append(f"scale=w='min(iw,{max_width})':h=-2")
    if max_fps > 0:
        filters.append(f"fps=fps={max_fps}")
    video_filter = ["-vf", ",".join(filters)] if filters else []
    attempts = [
        ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"],
        ["-c:v", "mpeg4", "-q:v", "5"],
    ]
    errors: list[str] = []
    for encoder_args in attempts:
        result = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                input_path,
                "-t",
                str(seconds),
                "-map",
                "0:v:0",
                "-an",
                *video_filter,
                *encoder_args,
                sample_path,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )
        sample_ready = os.path.isfile(sample_path) and os.path.getsize(sample_path) > 0
        if result.returncode == 0 and sample_ready:
            return
        errors.append(result.stdout.strip())
    raise RuntimeError("FFmpeg 无法创建短样本: " + " | ".join(error for error in errors if error))


def _probe_video(ffprobe: str | None, output_path: str) -> dict[str, Any]:
    if not ffprobe:
        return {"ok": True, "warning": "未安装 ffprobe，仅验证了输出文件非空。"}
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration,size,format_name",
            "-of",
            "json",
            output_path,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        return {"ok": False, "error": result.stdout.strip() or "ffprobe 验证失败"}
    try:
        metadata = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": "ffprobe 返回了无效 JSON"}
    return {"ok": True, "metadata": metadata.get("format") or {}}


def run_lada_smoke_test(
    config: dict[str, Any],
    input_path: str,
    *,
    profile_name: str | None = None,
    seconds: int = 3,
    max_width: int = 640,
    max_fps: int = 5,
    timeout: int = 1800,
    keep_output: bool = False,
    output_path: str | None = None,
) -> dict[str, Any]:
    source = os.path.abspath(os.path.expanduser(os.path.expandvars(str(input_path or ""))))
    if not os.path.isfile(source):
        raise ValueError(f"冒烟测试输入文件不存在: {source}")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("短视频冒烟测试需要 ffmpeg")
    ffprobe = shutil.which("ffprobe")
    cli = resolve_lada_executable(config.get("lada_cli_path"))
    if not cli:
        raise RuntimeError("找不到 Lada CLI")
    options, applied_profiles = merge_lada_options_for_path(config, source, profile_name)
    seconds = max(1, min(int(seconds), 30))
    max_width = max(0, min(int(max_width), 16384))
    max_fps = max(0, min(int(max_fps), 240))
    timeout = max(30, min(int(timeout), 86400))
    temp_parent = str(options.get("temporary_directory") or "").strip() or None
    if temp_parent and not os.path.isdir(temp_parent):
        raise ValueError(f"临时目录不存在: {temp_parent}")

    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="lada-agent-smoke-", dir=temp_parent) as temp_dir:
        sample_path = os.path.join(temp_dir, "sample.mp4")
        _make_short_sample(ffmpeg, source, sample_path, seconds, max_width, max_fps)
        if keep_output:
            if output_path:
                final_output = os.path.abspath(os.path.expanduser(os.path.expandvars(output_path)))
            else:
                timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                final_output = str(
                    Path(source).with_name(f"{Path(source).stem}.smoke-{timestamp}.mp4")
                )
            os.makedirs(os.path.dirname(final_output), exist_ok=True)
        else:
            final_output = os.path.join(temp_dir, "restored.mp4")

        command = build_lada_command(cli, sample_path, final_output, options)
        preview = build_lada_command_preview(cli, sample_path, final_output, options)
        try:
            result = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            output = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
            raise RuntimeError(f"Lada 冒烟测试超过 {timeout} 秒。\n{output[-4000:]}") from exc

        exists = os.path.isfile(final_output)
        size = os.path.getsize(final_output) if exists else 0
        probe = (
            _probe_video(ffprobe, final_output)
            if size > 0
            else {"ok": False, "error": "没有输出文件"}
        )
        success = result.returncode == 0 and size > 0 and probe.get("ok", False)
        return {
            "success": success,
            "return_code": result.returncode,
            "duration_seconds": round(time.monotonic() - started, 2),
            "sample_seconds": seconds,
            "sample_max_width": max_width,
            "sample_max_fps": max_fps,
            "profile": " → ".join(applied_profiles) or "global",
            "command": preview["command"],
            "argv": command,
            "overrides": preview["overrides"],
            "output_path": final_output if keep_output and exists else "",
            "output_size": size,
            "probe": probe,
            "log_tail": result.stdout[-12000:],
        }
