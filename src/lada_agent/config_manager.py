#!/usr/bin/env python3
"""Shared configuration loading, defaults, and validation for LADA Agent."""

import copy
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

DEFAULT_LADA_OPTIONS: dict[str, Any] = {
    "device": "cuda:0",
    "fp16": True,
    "mosaic_restoration_model": "basicvsrpp-v1.2",
    "max_clip_length": 180,
    "mosaic_detection_model": "v4-fast",
    "detect_face_mosaics": False,
    "encoding_preset": "hevc-nvidia-gpu-hq",
    "encoder": "",
    "encoder_options": "",
    "mp4_fast_start": True,
    "temporary_directory": "",
    "output_file_pattern": "{orig_file_name}.restored.mp4",
    "mosaic_restoration_config_path": "",
}

DEFAULT_UPTIME_KUMA: dict[str, Any] = {
    "enabled": False,
    "push_url": "",
    "interval_seconds": 60,
    "status": "up",
    "message": "LADA Agent online",
}

DEFAULT_CONFIG: dict[str, Any] = {
    "monitor_directories": [],
    # Empty keeps the historical per-input ``Restored`` directory behavior.
    "output_directory": "",
    "lada_cli_path": "lada-cli.exe",
    "supported_extensions": [".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm"],
    "lada_options": DEFAULT_LADA_OPTIONS,
    "scan_interval": 10,
    "processed_files_log": "../data/processed_files.json",
    "log_level": "INFO",
    "log_file": "../logs/lada_monitor.log",
    "max_concurrent_processes": 2,
    "auto_start": True,
    "move_processed_files": False,
    "processed_files_destination": "",
    "output_directory_name": "Restored",
    "output_file_keywords": ["_demosaic", ".restored"],
    "temporary_file_extensions": [".part", ".crdownload", ".tmp", ".aria2", ".!qb"],
    "file_ready_check_interval": 2,
    "file_ready_retry_delay": 10,
    "file_ready_max_retries": 30,
    "uptime_kuma": DEFAULT_UPTIME_KUMA,
    "web_access_token": "",
    "web_socketio_client_url": "",
}

LADA_OPTION_SCHEMA = {
    "device": ("str", "cuda:0", 128),
    "fp16": ("bool", True, None),
    "mosaic_restoration_model": ("str", "", 128),
    "max_clip_length": ("int", 180, 86400),
    "mosaic_detection_model": ("str", "", 128),
    "detect_face_mosaics": ("bool", False, None),
    "encoding_preset": ("str", "", 128),
    "encoder": ("str", "", 128),
    "encoder_options": ("str", "", 1000),
    "mp4_fast_start": ("bool", True, None),
    "temporary_directory": ("str", "", 1000),
    "output_file_pattern": ("str", "{orig_file_name}.restored.mp4", 240),
    "mosaic_restoration_config_path": ("str", "", 1000),
}


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def runtime_base_dir() -> Path:
    return Path(__file__).resolve().parent


def resolve_config_path(config_path: str | None = None) -> str:
    if not config_path:
        config_path = os.environ.get("LADA_AGENT_CONFIG")
    if config_path:
        return os.path.abspath(os.path.expandvars(os.path.expanduser(config_path)))

    if getattr(sys, "frozen", False):
        base_dir = Path(sys.executable).resolve().parent
    else:
        base_dir = project_root()

    possible_paths = [
        base_dir / "config" / "config.json",
        base_dir / "config.json",
        base_dir.parent / "config" / "config.json",
        runtime_base_dir() / ".." / "config" / "config.json",
        Path.cwd() / "config" / "config.json",
        Path.cwd() / "config.json",
    ]

    for path in possible_paths:
        normalized = path.resolve()
        if normalized.exists():
            return str(normalized)
    if getattr(sys, "frozen", False):
        fallback = base_dir / "config" / "config.json"
    else:
        fallback = Path.cwd() / "config" / "config.json"
    return str(fallback.resolve())


def merge_config(config: dict[str, Any] | None) -> dict[str, Any]:
    merged = copy.deepcopy(DEFAULT_CONFIG)
    if isinstance(config, dict):
        merged.update(config)

    if not isinstance(merged.get("lada_options"), dict):
        merged["lada_options"] = {}
    lada_options = copy.deepcopy(DEFAULT_LADA_OPTIONS)
    lada_options.update(merged.get("lada_options", {}))
    merged["lada_options"] = lada_options

    if not isinstance(merged.get("uptime_kuma"), dict):
        merged["uptime_kuma"] = {}
    uptime_kuma = copy.deepcopy(DEFAULT_UPTIME_KUMA)
    uptime_kuma.update(merged.get("uptime_kuma", {}))
    merged["uptime_kuma"] = uptime_kuma

    return merged


def load_config(config_path: str | None = None) -> tuple[dict[str, Any], str]:
    resolved_path = resolve_config_path(config_path)
    try:
        with open(resolved_path, encoding="utf-8") as f:
            loaded = json.load(f)
    except FileNotFoundError:
        return {}, resolved_path
    except json.JSONDecodeError as e:
        print(f"配置文件格式错误: {e}")
        return {}, resolved_path

    return merge_config(loaded), resolved_path


def atomic_write_json(path: str, data: Any) -> None:
    """Durably replace a JSON file without exposing a partial document."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    temporary_path = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=directory,
            prefix=f".{Path(path).name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = handle.name
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path and os.path.exists(temporary_path):
            try:
                os.unlink(temporary_path)
            except OSError:
                pass


def save_config(config_path: str, config: dict[str, Any]) -> None:
    atomic_write_json(config_path, config)


def initialize_config(config_path: str | None = None, overwrite: bool = False) -> str:
    """Create a complete starter config that also works from an installed wheel."""
    resolved_path = resolve_config_path(config_path)
    if os.path.exists(resolved_path) and not overwrite:
        raise FileExistsError(resolved_path)
    save_config(resolved_path, merge_config({}))
    return resolved_path


def resolve_runtime_path(path: str, base_dir: str | None = None) -> str:
    if not path:
        return path
    expanded = os.path.expandvars(os.path.expanduser(path))
    if os.path.isabs(expanded):
        return os.path.normpath(expanded)
    base = base_dir or str(runtime_base_dir())
    return os.path.normpath(os.path.join(base, expanded))


def resolve_runtime_paths(config: dict[str, Any], base_dir: str | None = None) -> dict[str, Any]:
    for key in (
        "log_file",
        "processed_files_log",
        "output_directory",
        "processed_files_destination",
    ):
        value = config.get(key)
        if value and not os.path.isabs(value):
            config[key] = resolve_runtime_path(value, base_dir)
    return config


def normalize_path(path: str) -> str:
    return os.path.normpath(path) if path else path


def coerce_positive_int(value: Any, default: int, maximum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    parsed = max(1, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def coerce_string(value: Any, default: str = "", max_length: int = 1000) -> str:
    if value is None:
        return default
    text = str(value).strip()
    if not text:
        return default
    return text[:max_length]


def coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def clean_path_list(value: Any) -> list:
    if not isinstance(value, list):
        return []
    paths = []
    seen = set()
    for item in value:
        path = coerce_string(item, "", 1000)
        if not path:
            continue
        normalized = normalize_path(path)
        key = normalized.lower() if os.name == "nt" else normalized
        if key in seen:
            continue
        seen.add(key)
        paths.append(normalized)
    return paths


def clean_string_list(
    value: Any, default: list | None = None, max_items: int = 100, max_length: int = 128
) -> list:
    if not isinstance(value, list):
        return list(default or [])
    cleaned = []
    seen = set()
    for item in value[:max_items]:
        text = coerce_string(item, "", max_length)
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
    return cleaned


def clean_extension_list(value: Any, default: list | None = None) -> list:
    extensions = []
    for item in clean_string_list(value, default, 100, 32):
        ext = item.lower()
        if not ext.startswith("."):
            ext = f".{ext}"
        extensions.append(ext)
    return extensions


def sanitize_config_update(
    data: dict[str, Any], existing_config: dict[str, Any] | None = None
) -> dict[str, Any]:
    full_config = merge_config(existing_config or {})

    if "monitor_directories" in data:
        full_config["monitor_directories"] = clean_path_list(data.get("monitor_directories"))
    if "output_directory" in data:
        full_config["output_directory"] = normalize_path(
            coerce_string(data.get("output_directory"), "")
        )
    if "scan_interval" in data:
        full_config["scan_interval"] = coerce_positive_int(data.get("scan_interval"), 10, 86400)
    if "max_concurrent_processes" in data:
        full_config["max_concurrent_processes"] = coerce_positive_int(
            data.get("max_concurrent_processes"), 1, 16
        )
    if "file_ready_check_interval" in data:
        full_config["file_ready_check_interval"] = coerce_positive_int(
            data.get("file_ready_check_interval"), 2, 3600
        )
    if "file_ready_retry_delay" in data:
        full_config["file_ready_retry_delay"] = coerce_positive_int(
            data.get("file_ready_retry_delay"), 10, 86400
        )
    if "file_ready_max_retries" in data:
        full_config["file_ready_max_retries"] = coerce_positive_int(
            data.get("file_ready_max_retries"), 30, 1000
        )
    if "auto_start" in data:
        full_config["auto_start"] = coerce_bool(data.get("auto_start"))
    if "lada_cli_path" in data:
        full_config["lada_cli_path"] = coerce_string(data.get("lada_cli_path"), "lada-cli.exe")
    if "web_access_token" in data:
        full_config["web_access_token"] = coerce_string(data.get("web_access_token"), "", 256)
    if "output_directory_name" in data:
        full_config["output_directory_name"] = coerce_string(
            data.get("output_directory_name"), "Restored", 128
        )
    if "supported_extensions" in data:
        full_config["supported_extensions"] = clean_extension_list(
            data.get("supported_extensions"), DEFAULT_CONFIG["supported_extensions"]
        )
    if "output_file_keywords" in data:
        full_config["output_file_keywords"] = clean_string_list(
            data.get("output_file_keywords"), DEFAULT_CONFIG["output_file_keywords"], 100, 128
        )
    if "temporary_file_extensions" in data:
        full_config["temporary_file_extensions"] = clean_extension_list(
            data.get("temporary_file_extensions"), DEFAULT_CONFIG["temporary_file_extensions"]
        )

    if "uptime_kuma" in data and isinstance(data["uptime_kuma"], dict):
        uptime = full_config.get("uptime_kuma", {})
        incoming = data["uptime_kuma"]
        if "enabled" in incoming:
            uptime["enabled"] = coerce_bool(incoming.get("enabled"))
        if "push_url" in incoming:
            uptime["push_url"] = coerce_string(incoming.get("push_url"), "", 2000)
        if "interval_seconds" in incoming:
            uptime["interval_seconds"] = coerce_positive_int(
                incoming.get("interval_seconds"), 60, 86400
            )
        if "status" in incoming:
            uptime["status"] = coerce_string(incoming.get("status"), "up", 32)
        if "message" in incoming:
            uptime["message"] = coerce_string(incoming.get("message"), "LADA Agent online", 500)
        full_config["uptime_kuma"] = uptime

    if "lada_options" in data and isinstance(data["lada_options"], dict):
        for key, value in data["lada_options"].items():
            if key not in LADA_OPTION_SCHEMA:
                continue
            kind, default, limit = LADA_OPTION_SCHEMA[key]
            if kind == "bool":
                full_config["lada_options"][key] = coerce_bool(value)
            elif kind == "int":
                full_config["lada_options"][key] = coerce_positive_int(value, default, limit)
            else:
                full_config["lada_options"][key] = coerce_string(value, default, limit)

    return full_config
