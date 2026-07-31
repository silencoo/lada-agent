"""Lada CLI command construction, validation, and capability discovery."""

from __future__ import annotations

import ctypes
import os
import re
import shlex
import string
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

AUTO_VALUE = "auto"

# The Agent owns these arguments because changing them would break output tracking,
# turn a processing job into an information command, or escape the selected file.
PROTECTED_EXTRA_OPTIONS = {
    "--help",
    "--input",
    "--output",
    "--output-file-pattern",
    "--version",
}

CAPABILITY_COMMANDS = {
    "devices": "--list-devices",
    "encoding_presets": "--list-encoding-presets",
    "encoders": "--list-encoders",
    "restoration_models": "--list-mosaic-restoration-models",
    "detection_models": "--list-mosaic-detection-models",
}

SUPPORTED_STABLE_VERSION = "0.11.0"
SUPPORTED_VERSION_SERIES = (0, 11)


def is_auto(value: Any) -> bool:
    return value is None or str(value).strip().lower() in {"", AUTO_VALUE}


def optional_bool(value: Any) -> bool | None:
    """Normalize legacy booleans and the new auto/on/off values."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"", "auto", "default"}:
            return None
        if normalized in {"1", "true", "yes", "on", "enabled"}:
            return True
        if normalized in {"0", "false", "no", "off", "disabled"}:
            return False
    return bool(value)


def validate_output_file_pattern(pattern: Any) -> list[str]:
    value = str(pattern or "").strip()
    errors: list[str] = []
    if not value:
        return ["输出文件模式不能为空"]
    if "/" in value or "\\" in value:
        errors.append("输出文件模式只能是文件名，不能包含目录分隔符")

    fields: list[str] = []
    try:
        for _literal, field_name, format_spec, conversion in string.Formatter().parse(value):
            if field_name is None:
                continue
            fields.append(field_name)
            if field_name != "orig_file_name" or format_spec or conversion:
                errors.append("输出文件模式只支持原样占位符 {orig_file_name}")
                break
    except ValueError:
        errors.append("输出文件模式包含未闭合或无效的大括号")

    if "orig_file_name" not in fields:
        errors.append("输出文件模式必须包含 {orig_file_name}")
    if not Path(value.replace("{orig_file_name}", "sample")).suffix:
        errors.append("输出文件模式必须包含文件扩展名")
    return list(dict.fromkeys(errors))


def _split_windows_command_line(command_line: str) -> list[str]:
    argc = ctypes.c_int()
    command_line_to_argv = ctypes.windll.shell32.CommandLineToArgvW
    command_line_to_argv.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    command_line_to_argv.restype = ctypes.POINTER(ctypes.c_wchar_p)
    argv = command_line_to_argv(command_line, ctypes.byref(argc))
    if not argv:
        raise ValueError("无法解析附加命令行参数")
    try:
        return [argv[index] for index in range(argc.value)]
    finally:
        ctypes.windll.kernel32.LocalFree(argv)


def parse_extra_cli_args(value: Any) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    try:
        args = _split_windows_command_line(text) if os.name == "nt" else shlex.split(text)
    except (ValueError, OSError) as exc:
        raise ValueError(f"附加命令行参数无法解析: {exc}") from exc
    if any("\x00" in arg for arg in args):
        raise ValueError("附加命令行参数不能包含 NUL 字符")
    return args


def validate_extra_cli_args(value: Any) -> list[str]:
    try:
        args = parse_extra_cli_args(value)
    except ValueError as exc:
        return [str(exc)]

    errors: list[str] = []
    for arg in args:
        option = arg.split("=", 1)[0].lower()
        if option in PROTECTED_EXTRA_OPTIONS or option.startswith("--list-"):
            errors.append(f"附加命令行不能覆盖受保护参数 {option}")
    return list(dict.fromkeys(errors))


def validate_lada_options(options: dict[str, Any] | None) -> list[str]:
    opts = options or {}
    errors = validate_output_file_pattern(
        opts.get("output_file_pattern", "{orig_file_name}.restored.mp4")
    )
    errors.extend(validate_extra_cli_args(opts.get("extra_cli_args", "")))
    try:
        if int(opts.get("max_clip_length", 180)) < 1:
            errors.append("最大 clip 长度必须至少为 1 帧")
    except (TypeError, ValueError):
        errors.append("最大 clip 长度必须是整数")
    if opts.get("encoder_options") and not opts.get("encoder"):
        errors.append("填写 encoder_options 时必须同时填写 encoder")
    return list(dict.fromkeys(errors))


def build_lada_command(
    lada_cli_path: str,
    input_file: str,
    output_file: str,
    options: dict[str, Any] | None,
) -> list[str]:
    opts = options or {}
    errors = validate_lada_options(opts)
    if errors:
        raise ValueError("；".join(errors))

    command = [lada_cli_path, "--input", input_file, "--output", output_file]

    device = opts.get("device")
    if not is_auto(device):
        command.extend(["--device", str(device).strip()])

    fp16 = optional_bool(opts.get("fp16"))
    if fp16 is not None:
        command.append("--fp16" if fp16 else "--no-fp16")

    encoder = str(opts.get("encoder") or "").strip()
    encoder_options = str(opts.get("encoder_options") or "").strip()
    encoding_preset = opts.get("encoding_preset")
    if encoder:
        command.extend(["--encoder", encoder])
        if encoder_options:
            command.extend(["--encoder-options", encoder_options])
    elif not is_auto(encoding_preset):
        command.extend(["--encoding-preset", str(encoding_preset).strip()])

    command.append(
        "--mp4-fast-start" if bool(opts.get("mp4_fast_start")) else "--no-mp4-fast-start"
    )

    restoration_model = opts.get("mosaic_restoration_model")
    if not is_auto(restoration_model):
        command.extend(["--mosaic-restoration-model", str(restoration_model).strip()])
    max_clip_length = int(opts.get("max_clip_length", 180))
    command.extend(["--max-clip-length", str(max_clip_length)])

    detection_model = opts.get("mosaic_detection_model")
    if not is_auto(detection_model):
        command.extend(["--mosaic-detection-model", str(detection_model).strip()])
    command.append(
        "--detect-face-mosaics"
        if bool(opts.get("detect_face_mosaics"))
        else "--no-detect-face-mosaics"
    )

    temporary_directory = str(opts.get("temporary_directory") or "").strip()
    if temporary_directory:
        command.extend(["--temporary-directory", temporary_directory])

    restoration_config = str(opts.get("mosaic_restoration_config_path") or "").strip()
    if restoration_config:
        command.extend(["--mosaic-restoration-config-path", restoration_config])

    # Deliberately last: advanced users can override managed inference/export
    # options while the Agent retains ownership of input/output task boundaries.
    command.extend(parse_extra_cli_args(opts.get("extra_cli_args", "")))
    return command


def format_lada_command(command: list[str], platform: str | None = None) -> str:
    """Render argv for display/copying without changing how it is executed."""
    target = platform or ("windows" if os.name == "nt" else "posix")
    if target == "windows":
        return subprocess.list2cmdline(command)
    return shlex.join(command)


def _canonical_option(option: str) -> str:
    normalized = option.split("=", 1)[0].lower()
    if normalized.startswith("--no-"):
        return "--" + normalized[5:]
    return normalized


def build_lada_command_preview(
    lada_cli_path: str,
    input_file: str,
    output_file: str,
    options: dict[str, Any] | None,
    platform: str | None = None,
) -> dict[str, Any]:
    opts = options or {}
    base_opts = {**opts, "extra_cli_args": ""}
    base_command = build_lada_command(lada_cli_path, input_file, output_file, base_opts)
    command = build_lada_command(lada_cli_path, input_file, output_file, opts)
    base_options = {arg for arg in base_command if arg.startswith("--")}
    extra_options = [
        arg for arg in parse_extra_cli_args(opts.get("extra_cli_args")) if arg.startswith("--")
    ]
    canonical_base = {_canonical_option(arg) for arg in base_options}
    overrides = [arg for arg in extra_options if _canonical_option(arg) in canonical_base]
    return {
        "argv": command,
        "command": format_lada_command(command, platform),
        "overrides": list(dict.fromkeys(overrides)),
        "shell": False,
    }


def evaluate_lada_version(output: str) -> dict[str, Any]:
    """Classify an upstream Lada version without overstating runtime validation."""
    match = re.search(r"(?<!\d)(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.-]+)?", output or "")
    if not match:
        return {
            "version": "",
            "status": "unknown",
            "label": "无法识别版本",
            "message": "CLI 可运行，但版本输出无法与兼容矩阵匹配。",
            "latest_stable": SUPPORTED_STABLE_VERSION,
        }

    version = match.group(0)
    major, minor, patch = (int(match.group(i)) for i in range(1, 4))
    is_dev = "dev" in version.lower()
    if (major, minor, patch) == (0, 11, 0) and not is_dev:
        status = "source-verified"
        label = "源码参数契约已验证"
        message = "与 Lada v0.11.0 的 CLI 源码参数契约一致；真实硬件仍以冒烟测试为准。"
    elif (major, minor) == SUPPORTED_VERSION_SERIES:
        status = "compatible-unverified"
        label = "兼容但未完整回归"
        message = "属于 0.11 系列，当前 CLI 表面兼容；建议执行短视频冒烟测试。"
    elif (major, minor) == (0, 10):
        status = "legacy-compatible"
        label = "旧版兼容模式"
        message = "0.10 系列具备核心参数，但不包含 0.11 的全部硬件与修复改进。"
    elif (major, minor) < (0, 10):
        status = "unsupported"
        label = "版本过旧"
        message = "此版本早于 Agent 的参数基线，不建议用于自动任务。"
    else:
        status = "future-unverified"
        label = "未来版本待验证"
        message = "版本新于当前兼容基线；请先检查命令预览并执行冒烟测试。"
    return {
        "version": version,
        "status": status,
        "label": label,
        "message": message,
        "latest_stable": SUPPORTED_STABLE_VERSION,
    }


def _subprocess_startup_info():
    if os.name != "nt":
        return None
    startup_info = subprocess.STARTUPINFO()
    startup_info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return startup_info


def _table_values(output: str, validator: Callable[[str], bool] | None = None) -> list[str]:
    rows: list[list[str]] = []
    for line in output.splitlines():
        if not line.startswith("  "):
            continue
        columns = [column.strip() for column in re.split(r"\s{2,}", line.strip())]
        if columns and columns[0]:
            rows.append(columns)
    values = [row[0] for row in rows[1:]] if len(rows) > 1 else []
    values = [value for value in values if not re.fullmatch(r"-+", value)]
    if validator:
        values = [value for value in values if validator(value)]
    return list(dict.fromkeys(values))


def _run_capability_command(
    lada_cli_path: str, arguments: str | list[str], timeout: float
) -> dict[str, Any]:
    args = [arguments] if isinstance(arguments, str) else arguments
    try:
        result = subprocess.run(
            [lada_cli_path, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            startupinfo=_subprocess_startup_info(),
            check=False,
        )
    except FileNotFoundError:
        return {"ok": False, "values": [], "output": "", "error": "找不到 Lada CLI"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "values": [], "output": "", "error": "Lada CLI 探测超时"}
    except OSError as exc:
        return {"ok": False, "values": [], "output": "", "error": str(exc)}

    output = result.stdout.strip()
    values = _table_values(output)
    return {
        "ok": result.returncode == 0,
        "values": values,
        "output": output,
        "error": "" if result.returncode == 0 else (output or f"退出码 {result.returncode}"),
    }


def discover_lada_capabilities(lada_cli_path: str, timeout: float = 30) -> dict[str, Any]:
    path = str(lada_cli_path or "lada-cli").strip() or "lada-cli"
    version_result = _run_capability_command(path, "--version", timeout)
    capabilities = {
        name: _run_capability_command(path, option, timeout)
        for name, option in CAPABILITY_COMMANDS.items()
    }
    successful = sum(1 for result in capabilities.values() if result["ok"])
    return {
        "ok": version_result["ok"] and successful > 0,
        "cli_path": path,
        "version": version_result["output"],
        "compatibility": evaluate_lada_version(version_result["output"]),
        "version_error": version_result["error"],
        "capabilities": capabilities,
    }


def discover_lada_encoder_options(
    lada_cli_path: str, encoder: str, timeout: float = 30
) -> dict[str, Any]:
    encoder_name = str(encoder or "").strip()
    if (
        not encoder_name
        or len(encoder_name) > 128
        or any(char in encoder_name for char in {"\x00", "\r", "\n"})
    ):
        return {"ok": False, "values": [], "output": "", "error": "编码器名称无效"}
    path = str(lada_cli_path or "lada-cli").strip() or "lada-cli"
    return _run_capability_command(path, ["--list-encoder-options", encoder_name], timeout)
