#!/usr/bin/env python3
"""
LADA视频文件自动监控和处理程序
自动监控指定目录，发现新视频文件后调用 lada-cli 进行 demosaic 处理
"""

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

from lada_agent.config_manager import (
    atomic_write_json,
    load_config,
    resolve_runtime_path,
    resolve_runtime_paths,
    validate_config,
)
from lada_agent.diagnostics import diagnose_environment
from lada_agent.instance_lock import MonitorInstanceLock
from lada_agent.lada_client import build_lada_command, format_lada_command, validate_lada_options

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
except ImportError:
    print("需要安装watchdog库: pip install watchdog")
    exit(1)


class LadaMonitor:
    def __init__(self, config_path: str = None):
        print("初始化LadaMonitor...")
        self.config_path = None
        self.processed_files_lock = threading.RLock()
        self.queue_lock = threading.RLock()
        self._status_print_lock = threading.Lock()
        self._last_status_state: tuple[int, int, int] | None = None
        self.processed_files = {}
        self.processing_files: set[str] = set()
        self.processing_queue: list[str] = []
        self.processing_progress: dict[str, int] = {}
        self.queued_profiles: dict[str, str] = {}
        self.is_processing = False
        self.should_stop = False
        self.max_concurrent_processes = 1
        self.active_workers = 0
        self.worker_threads: list[threading.Thread] = []
        self.active_processes: dict[str, subprocess.Popen] = {}
        self.active_processes_lock = threading.RLock()
        self.observer = None
        self.event_handler = None
        self.web_callbacks: dict[str, Callable] = {}
        self.uptime_kuma_thread: threading.Thread | None = None
        self.uptime_kuma_stop = threading.Event()
        self.file_retry_lock = threading.RLock()
        self.file_retry_counts: dict[str, int] = {}
        self.file_retry_timers: dict[str, threading.Timer] = {}
        self.hash_cache: dict[str, tuple[int, int, str]] = {}
        self.candidate_lock = threading.RLock()
        self.candidate_paths: set[str] = set()
        self.candidate_executor: ThreadPoolExecutor | None = None
        self.logger = logging.getLogger("LadaMonitor")
        self.instance_lock: MonitorInstanceLock | None = None
        self.last_preflight_report: dict | None = None
        self._preflight_completed = False
        self.config = self._load_config(config_path)
        print(f"配置加载完成: {bool(self.config)}")

        if not self.config:
            print("配置加载失败，程序退出")
            return

        # Relative log/state paths must be fixed before loading state or setting up logging.
        print("修复相对路径...")
        self._fix_relative_paths()
        self.instance_lock = MonitorInstanceLock(
            os.path.join(
                os.path.dirname(self.config_path),
                f".{Path(self.config_path).name}.lada-monitor.lock",
            )
        )

        self.processed_files = self._load_processed_files()
        self.max_concurrent_processes = max(
            1, int(self.config.get("max_concurrent_processes", 1) or 1)
        )
        self.candidate_executor = ThreadPoolExecutor(
            max_workers=min(4, max(2, self.max_concurrent_processes)),
            thread_name_prefix="lada-candidate",
        )

        # 设置日志
        print("设置日志系统...")
        self._setup_logging()

    def set_web_callback(self, event: str, callback: Callable):
        """设置Web界面回调函数"""
        self.web_callbacks[event] = callback

    def _emit_web_event(self, event: str, data: dict = None):
        """发送Web事件"""
        if event in self.web_callbacks:
            try:
                self.web_callbacks[event](data or {})
            except Exception as e:
                self.logger.error(f"Web回调执行失败 {event}: {e}")

    def _build_uptime_kuma_url(self, base_url: str, params: dict[str, str]) -> str:
        """构建Uptime Kuma Push URL（合并已有查询参数）"""
        parsed = urlparse(base_url)
        existing = dict(parse_qsl(parsed.query, keep_blank_values=True))
        for key, value in params.items():
            if value is None:
                continue
            existing[key] = str(value)
        query = urlencode(existing, doseq=True)
        return urlunparse(parsed._replace(query=query))

    def _format_uptime_kuma_message(self, template: str) -> str:
        """格式化Uptime Kuma消息，支持简单占位符"""
        if not template:
            return ""
        try:
            with self.queue_lock:
                queue_size = len(self.processing_queue)
                processing = len(self.processing_files)
            with self.processed_files_lock:
                processed = len(self.processed_files)
            return template.format(queue=queue_size, processing=processing, processed=processed)
        except Exception:
            return template

    def _send_uptime_kuma_ping(self, status: str, message: str):
        """发送Uptime Kuma心跳"""
        cfg = self.config.get("uptime_kuma", {})
        push_url = (cfg.get("push_url") or "").strip()
        if not push_url:
            return

        full_url = self._build_uptime_kuma_url(
            push_url, {"status": status or "up", "msg": message or ""}
        )

        try:
            req = Request(full_url, method="GET")
            with urlopen(req, timeout=10) as resp:
                resp.read(64)
            self.logger.debug("Uptime Kuma heartbeat sent")
        except Exception as e:
            self.logger.warning(f"Uptime Kuma heartbeat failed: {e}")

    def _parse_progress_percent(self, line: str) -> int | None:
        """从LADA输出中解析百分比进度"""
        if not line or "%" not in line:
            return None
        # 尽量匹配类似 "Processing video:   4%|▍ ..." 或 "Processing frames ..."
        if ("Processing" not in line) and ("Processed:" not in line) and ("Remaining" not in line):
            return None
        match = re.search(r"(\d{1,3})\s*%", line)
        if not match:
            return None
        try:
            return min(100, int(match.group(1)))
        except Exception:
            return None

    def _uptime_kuma_loop(self):
        cfg = self.config.get("uptime_kuma", {})
        interval = int(cfg.get("interval_seconds", 60) or 60)
        if interval < 1:
            interval = 1
        status = cfg.get("status", "up")
        message_template = cfg.get("message", "LADA Agent online")

        while not self.should_stop and not self.uptime_kuma_stop.is_set():
            message = self._format_uptime_kuma_message(message_template)
            self._send_uptime_kuma_ping(status, message)
            self.uptime_kuma_stop.wait(interval)

    def _start_uptime_kuma_heartbeat(self):
        cfg = self.config.get("uptime_kuma", {})
        if not cfg.get("enabled"):
            return
        if not (cfg.get("push_url") or "").strip():
            self.logger.warning("Uptime Kuma已启用但未配置push_url")
            return
        if self.uptime_kuma_thread and self.uptime_kuma_thread.is_alive():
            return
        self.uptime_kuma_stop.clear()
        self.uptime_kuma_thread = threading.Thread(target=self._uptime_kuma_loop, daemon=True)
        self.uptime_kuma_thread.start()

    def _stop_uptime_kuma_heartbeat(self):
        self.uptime_kuma_stop.set()
        if self.uptime_kuma_thread and self.uptime_kuma_thread.is_alive():
            self.uptime_kuma_thread.join(timeout=2)

    def _terminate_active_processes(self, timeout: float = 10):
        """Terminate running lada-cli subprocesses when the caller requests a force stop."""
        with self.active_processes_lock:
            processes = list(self.active_processes.items())

        for file_path, process in processes:
            if process.poll() is not None:
                continue
            self.logger.warning(f"正在终止运行中的LADA进程: {file_path}")
            try:
                process.terminate()
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.logger.warning(f"LADA进程未及时退出，强制结束: {file_path}")
                process.kill()
                process.wait(timeout=5)
            except Exception as e:
                self.logger.error(f"终止LADA进程失败 {file_path}: {e}")

    def stop_monitoring(self, terminate_running: bool = False):
        """停止监控"""
        print("🛑 正在停止监控...")
        self.should_stop = True
        self._cancel_file_ready_retries()
        self._stop_uptime_kuma_heartbeat()
        if terminate_running:
            self._terminate_active_processes()

        if self.candidate_executor:
            self.candidate_executor.shutdown(wait=False, cancel_futures=True)
            self.candidate_executor = None

        if self.observer:
            self.observer.stop()
            self.observer.join()
        current_thread = threading.current_thread()
        for worker in list(self.worker_threads):
            if worker is not current_thread and worker.is_alive():
                worker.join(timeout=15 if terminate_running else 1)
        with self.queue_lock:
            self.is_processing = self.active_workers > 0

        self._emit_web_event("monitoring_stopped")
        print("✅ 监控已停止")

    def acquire_instance_lock(self) -> bool:
        """Reserve the configured monitor for this process."""
        return bool(self.instance_lock and self.instance_lock.acquire())

    def release_instance_lock(self) -> None:
        if self.instance_lock:
            self.instance_lock.release()

    def _config_int(self, key: str, default: int, minimum: int = 0) -> int:
        try:
            value = int(self.config.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(minimum, value)

    def _path_key(self, file_path: str) -> str:
        return os.path.normcase(os.path.abspath(os.path.normpath(file_path)))

    def _output_dir_name(self) -> str:
        name = str(self.config.get("output_directory_name") or "Restored").strip()
        return name or "Restored"

    def _output_dir_for_file(self, file_path: str) -> str:
        configured = str(self.config.get("output_directory") or "").strip()
        if configured:
            return os.path.normpath(configured)
        return os.path.join(os.path.dirname(file_path), self._output_dir_name())

    def _directory_profile_for_file(self, file_path: str) -> str | None:
        mappings = self.config.get("directory_profiles") or {}
        if not isinstance(mappings, dict):
            return None
        file_key = self._path_key(file_path)
        matches: list[tuple[int, str]] = []
        for directory, profile_name in mappings.items():
            try:
                directory_key = self._path_key(str(directory))
                if os.path.commonpath([file_key, directory_key]) == directory_key:
                    matches.append((len(directory_key), str(profile_name)))
            except (OSError, ValueError):
                continue
        return max(matches, default=(0, ""))[1] or None

    def _lada_options_for_file(self, file_path: str) -> tuple[dict, str | None]:
        key = self._path_key(file_path)
        with self.queue_lock:
            task_profile = self.queued_profiles.get(key)
        directory_profile = self._directory_profile_for_file(file_path)
        options = dict(self.config.get("lada_options") or {})
        applied_profiles: list[str] = []
        for profile_name in (directory_profile, task_profile):
            if not profile_name or profile_name in applied_profiles:
                continue
            profiles = self.config.get("lada_profiles") or {}
            overrides = profiles.get(profile_name)
            if not isinstance(overrides, dict):
                raise ValueError(f"Lada 参数模板不存在或无效: {profile_name}")
            options.update(overrides)
            applied_profiles.append(profile_name)
        errors = validate_lada_options(options)
        if errors:
            raise ValueError("；".join(errors))
        return options, " → ".join(applied_profiles) or None

    def _output_path_for_file(self, file_path: str, options: dict | None = None) -> str:
        path = Path(file_path)
        pattern = str(
            (options or self.config.get("lada_options", {})).get("output_file_pattern")
            or "{orig_file_name}.restored.mp4"
        )
        output_name = pattern.format(orig_file_name=path.stem).strip()
        return os.path.join(self._output_dir_for_file(file_path), output_name)

    def _output_file_keywords(self) -> list[str]:
        keywords = self.config.get("output_file_keywords", ["_demosaic", ".restored"])
        if not isinstance(keywords, list):
            return ["_demosaic", ".restored"]
        return [str(keyword).lower() for keyword in keywords if str(keyword).strip()]

    def _temporary_file_extensions(self) -> set[str]:
        extensions = self.config.get("temporary_file_extensions", [])
        if not isinstance(extensions, list):
            return set()
        return {str(ext).lower() for ext in extensions if str(ext).strip()}

    def _clear_file_ready_retry(self, file_path: str):
        key = self._path_key(file_path)
        with self.file_retry_lock:
            timer = self.file_retry_timers.pop(key, None)
            self.file_retry_counts.pop(key, None)
        if timer:
            timer.cancel()

    def _cancel_file_ready_retries(self):
        with self.file_retry_lock:
            timers = list(self.file_retry_timers.values())
            self.file_retry_timers.clear()
            self.file_retry_counts.clear()
        for timer in timers:
            timer.cancel()

    def _retry_file_ready(self, file_path: str, key: str):
        with self.file_retry_lock:
            self.file_retry_timers.pop(key, None)
        if self.should_stop:
            return
        self.logger.debug(f"重新检查文件是否就绪: {file_path}")
        self.process_new_file(file_path)

    def _schedule_file_ready_retry(self, file_path: str):
        max_retries = self._config_int("file_ready_max_retries", 30, 0)
        if max_retries <= 0:
            self.logger.debug(f"文件尚未就绪且重试已禁用: {file_path}")
            return

        key = self._path_key(file_path)
        delay = self._config_int("file_ready_retry_delay", 10, 1)
        with self.file_retry_lock:
            if key in self.file_retry_timers:
                return
            retry_count = self.file_retry_counts.get(key, 0)
            if retry_count >= max_retries:
                self.file_retry_counts.pop(key, None)
                self.logger.warning(f"文件多次未就绪，停止重试: {file_path}")
                return

            self.file_retry_counts[key] = retry_count + 1
            timer = threading.Timer(delay, self._retry_file_ready, args=(file_path, key))
            timer.daemon = True
            self.file_retry_timers[key] = timer

        self.logger.debug(
            f"文件尚未就绪，{delay}秒后重试({retry_count + 1}/{max_retries}): {file_path}"
        )
        timer.start()

    def _load_config(self, config_path: str) -> dict:
        """加载配置文件"""
        config, resolved_path = load_config(config_path)
        self.config_path = resolved_path
        if not config:
            print(f"配置文件不存在或无法加载: {resolved_path}")
            return {}
        errors = validate_config(config)
        if errors:
            print(f"Lada 参数配置无效: {'；'.join(errors)}")
            return {}
        return config

    def _resolve_runtime_path(self, path: str) -> str:
        """将配置中的运行时文件路径解析到脚本目录下，保持旧配置兼容。"""
        return resolve_runtime_path(path)

    def _fix_relative_paths(self):
        """修复相对路径"""
        # Runtime paths in config.json are relative to that file.  Resolving
        # them from the installed package directory breaks source checkouts
        # (and packaged layouts) that keep state in ../data and ../logs.
        resolve_runtime_paths(self.config, base_dir=os.path.dirname(self.config_path))

    def _load_processed_files(self) -> dict[str, str]:
        """加载已处理文件记录"""
        try:
            processed_log_path = self.config["processed_files_log"]
            if os.path.exists(processed_log_path):
                with open(processed_log_path, encoding="utf-8") as f:
                    data = json.load(f)
                    return data if isinstance(data, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        return {}

    def _save_processed_files(self):
        """保存已处理文件记录"""
        try:
            with self.processed_files_lock:
                processed_log_path = self.config["processed_files_log"]
                atomic_write_json(processed_log_path, self.processed_files)
        except Exception as e:
            self.logger.error(f"保存处理记录失败: {e}")
            raise

    def _queue_snapshot(self) -> dict:
        """生成线程安全的队列状态快照。"""
        with self.queue_lock:
            return {
                "queue_size": len(self.processing_queue),
                "processing_files": [os.path.basename(f) for f in self.processing_files],
                "queue": [os.path.basename(f) for f in self.processing_queue],
                "processing_progress": {
                    os.path.basename(path): pct for path, pct in self.processing_progress.items()
                },
            }

    def _emit_queue_updated(self):
        """向Web端推送最新队列状态。"""
        self._emit_web_event("queue_updated", self._queue_snapshot())
        self._print_status()

    def _print_status(self):
        """仅在队列/处理/已处理数量发生变化时打印状态，避免重复刷屏。"""
        with self.queue_lock:
            queue_size = len(self.processing_queue)
            processing_count = len(self.processing_files)
        with self.processed_files_lock:
            processed_count = len(self.processed_files)

        state = (processed_count, queue_size, processing_count)
        with self._status_print_lock:
            if self._last_status_state == state:
                return
            self._last_status_state = state
            print(
                f"💤 监控中... 已处理文件: {processed_count}, "
                f"队列中: {queue_size}, 正在处理: {processing_count}"
            )

    def _setup_logging(self):
        """设置日志"""
        if not self.config or "log_level" not in self.config:
            # 如果配置还没加载完成，使用基本日志配置
            self.logger = logging.getLogger("LadaMonitor")
            self.logger.setLevel(logging.INFO)
            if not self.logger.handlers:
                handler = logging.StreamHandler()
                handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
                self.logger.addHandler(handler)
            return

        log_level = getattr(logging, self.config["log_level"].upper(), logging.INFO)
        log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

        # 创建日志目录
        log_file_path = self.config["log_file"]
        log_dir = os.path.dirname(log_file_path)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)

        # 创建独立的LadaMonitor日志记录器（不干扰其他日志）
        self.logger = logging.getLogger("LadaMonitor")
        self.logger.setLevel(log_level)

        # 清除可能存在的旧处理器并关闭文件句柄
        for handler in self.logger.handlers[:]:
            self.logger.removeHandler(handler)
            handler.close()

        # 只添加文件处理器和控制台处理器
        file_handler = logging.FileHandler(log_file_path, encoding="utf-8")
        file_handler.setLevel(log_level)
        file_handler.setFormatter(logging.Formatter(log_format))

        console_handler = logging.StreamHandler()
        console_handler.setLevel(log_level)
        console_handler.setFormatter(logging.Formatter(log_format))

        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)

        # 防止日志传播到根记录器（避免干扰Flask日志）
        self.logger.propagate = False

    def _get_file_hash(self, file_path: str) -> str:
        """计算文件哈希值"""
        try:
            stat = os.stat(file_path)
            key = self._path_key(file_path)
            cached = self.hash_cache.get(key)
            signature = (stat.st_size, stat.st_mtime_ns)
            if cached and cached[:2] == signature:
                return cached[2]
            hash_md5 = hashlib.md5()
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    hash_md5.update(chunk)
            digest = hash_md5.hexdigest()
            self.hash_cache[key] = (signature[0], signature[1], digest)
            return digest
        except Exception as e:
            self.logger.error(f"计算文件哈希失败 {file_path}: {e}")
            return ""

    def _is_video_file(self, file_path: str) -> bool:
        """检查是否为支持的视频文件"""
        if not os.path.isfile(file_path):
            return False

        file_name_lower = Path(file_path).name.lower()
        if any(file_name_lower.endswith(ext) for ext in self._temporary_file_extensions()):
            return False

        ext = Path(file_path).suffix.lower()
        return ext in self.config["supported_extensions"]

    def _is_file_ready(self, file_path: str) -> bool:
        """检查文件是否已完成写入（大小不再变化）"""
        try:
            stable_seconds = self._config_int("file_ready_check_interval", 2, 1)
            initial_size = os.path.getsize(file_path)
            time.sleep(stable_seconds)
            final_size = os.path.getsize(file_path)
            return initial_size == final_size and final_size > 0
        except OSError:
            return False

    def _is_in_output_directory(self, file_path: str) -> bool:
        configured = str(self.config.get("output_directory") or "").strip()
        if configured:
            try:
                return os.path.commonpath(
                    [os.path.abspath(file_path), os.path.abspath(configured)]
                ) == os.path.abspath(configured)
            except (OSError, ValueError):
                return False
        output_dir_name = self._output_dir_name().lower()
        try:
            path = Path(file_path)
            return any(parent.name.lower() == output_dir_name for parent in path.parents)
        except Exception:
            return False

    def _is_matching_output_name(self, input_file: str, output_name: str) -> bool:
        input_stem = Path(input_file).stem.lower()
        output_stem = Path(output_name).stem.lower()
        for keyword in self._output_file_keywords():
            prefix = f"{input_stem}{keyword}"
            if output_stem == prefix:
                return True
            if output_stem.startswith(prefix) and output_stem[len(prefix) : len(prefix) + 1] in {
                ".",
                "-",
                "_",
            }:
                return True
        return False

    def _candidate_output_names(self, file_path: str, options: dict | None = None) -> set[str]:
        path = Path(file_path)
        names = {
            os.path.basename(self._output_path_for_file(file_path, options)),
            f"{path.stem}_demosaic{path.suffix}",
            f"{path.stem}.restored{path.suffix}",
            f"{path.stem}.restored.mp4",
        }
        return {name.lower() for name in names}

    def _find_existing_output(self, file_path: str, options: dict | None = None) -> str | None:
        output_dir = self._output_dir_for_file(file_path)
        if not os.path.isdir(output_dir):
            return None

        exact_names = self._candidate_output_names(file_path, options)
        for existing_file in os.listdir(output_dir):
            existing_path = os.path.join(output_dir, existing_file)
            if not os.path.isfile(existing_path):
                continue
            existing_file_lower = existing_file.lower()
            if existing_file_lower in exact_names or self._is_matching_output_name(
                file_path, existing_file
            ):
                return existing_path
        return None

    def _is_output_file(self, file_path: str) -> bool:
        """检查文件是否是由程序输出的文件"""
        try:
            file_name = os.path.basename(file_path)
            file_name_lower = file_name.lower()

            if self._is_in_output_directory(file_path):
                return True

            # 检查文件名是否包含任一关键字
            for keyword in self._output_file_keywords():
                if keyword in file_name_lower:
                    return True

            return False
        except Exception:
            return False

    def _is_already_processed(self, file_path: str) -> bool:
        """检查文件是否已经处理过（通过检查输出目录）"""
        try:
            if self._has_recorded_path(file_path):
                return True
            options, _profile_name = self._lada_options_for_file(file_path)
            if self._find_existing_output(file_path, options) is not None:
                return True
            file_hash = self._get_file_hash(file_path)
            if file_hash:
                with self.processed_files_lock:
                    if file_hash in self.processed_files:
                        return True
            return False
        except Exception as e:
            self.logger.error(f"检查文件是否已处理时出错 {file_path}: {e}")
            return False

    def _has_recorded_path(self, file_path: str) -> bool:
        """Fast path for unchanged files already represented by usable history."""
        try:
            size = os.path.getsize(file_path)
        except OSError:
            return False
        key = self._path_key(file_path)
        with self.processed_files_lock:
            for info in self.processed_files.values():
                if not isinstance(info, dict):
                    continue
                original_path = info.get("original_path", "")
                output_path = info.get("output_path", "")
                try:
                    recorded_size = int(info.get("file_size", -1) or -1)
                except (TypeError, ValueError):
                    continue
                if (
                    original_path
                    and self._path_key(original_path) == key
                    and recorded_size == size
                    and output_path
                    and os.path.isfile(output_path)
                ):
                    return True
        return False

    def _build_lada_command(
        self, input_file: str, output_file: str, options: dict | None = None
    ) -> list[str]:
        """构建lada-cli命令"""
        return build_lada_command(
            self.config["lada_cli_path"],
            input_file,
            output_file,
            options or self.config.get("lada_options", {}),
        )

    def _process_video_file(self, file_path: str):
        """处理单个视频文件"""
        file_path = os.path.normpath(file_path)
        file_hash = self._get_file_hash(file_path)
        if not file_hash:
            self.logger.debug(f"哈希计算失败，跳过: {file_path}")
            return
        with self.processed_files_lock:
            if file_hash in self.processed_files:
                self.logger.debug(f"文件已处理过，跳过: {file_path}")
                return

        file_name = os.path.basename(file_path)
        options, profile_name = self._lada_options_for_file(file_path)
        with self.queue_lock:
            self.queued_profiles.pop(self._path_key(file_path), None)

        # 动态生成输出目录：[InputFileDir]/Restored
        output_dir = self._output_dir_for_file(file_path)

        # 确保输出目录存在
        try:
            os.makedirs(output_dir, exist_ok=True)
        except Exception as e:
            self.logger.error(f"无法创建输出目录 {output_dir}: {e}")
            print(f"❌ 无法创建输出目录: {output_dir}")
            return

        # 输出文件路径
        output_path = self._output_path_for_file(file_path, options)

        existing_output = self._find_existing_output(file_path, options)
        if existing_output:
            self.logger.info(f"输出文件已存在，跳过处理: {file_path} (输出文件: {existing_output})")
            print(
                f"⏭️  文件已处理过（输出文件已存在）: {file_name} "
                f"-> {os.path.basename(existing_output)}"
            )
            with self.processed_files_lock:
                self.processed_files[file_hash] = {
                    "original_path": file_path,
                    "output_path": existing_output,
                    "processed_time": datetime.fromtimestamp(
                        os.path.getmtime(existing_output)
                    ).isoformat(),
                    "file_size": os.path.getsize(file_path),
                    "detected_existing": True,
                }
                self._save_processed_files()
            return

        print(f"\n🎬 开始处理视频文件: {file_name}")
        self.logger.info(f"开始处理视频文件: {file_path}")

        # 添加到正在处理的文件集合
        with self.queue_lock:
            self.processing_files.add(file_path)
            self.processing_progress[file_path] = 0
        self._emit_queue_updated()

        # 发送Web事件
        self._emit_web_event(
            "processing_started",
            {
                "file": file_name,
                "path": file_path,
                "profile": profile_name or "global",
                "time": datetime.now().isoformat(),
            },
        )

        # 构建命令
        cmd = self._build_lada_command(file_path, output_path, options)

        try:
            print("⚙️  执行LADA处理命令...")
            display_command = format_lada_command(cmd)
            print(f"📋 命令: {display_command}")
            self.logger.info(f"执行命令: {display_command}")

            # 执行命令并实时显示输出
            print("\n🚀 开始处理，实时输出:")
            print("=" * 60)

            with self.active_processes_lock:
                if self.should_stop:
                    self.logger.info(f"监控已停止，取消尚未启动的处理: {file_path}")
                    return
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    universal_newlines=True,
                )
                self.active_processes[file_path] = process

            # 实时读取并显示输出
            while True:
                output = process.stdout.readline()
                if output == "" and process.poll() is not None:
                    break
                if output:
                    # 检查是否是进度条输出（包含%和|）
                    line = output.strip()
                    percent = self._parse_progress_percent(line)
                    if percent is not None:
                        with self.queue_lock:
                            self.processing_progress[file_path] = percent
                        self._emit_web_event(
                            "processing_progress",
                            {
                                "file": file_name,
                                "path": file_path,
                                "percent": percent,
                                "time": datetime.now().isoformat(),
                            },
                        )
                        # 进度条输出，使用\r回到行首，不换行
                        print(f"\r{line}", end="", flush=True)
                    else:
                        # 其他输出，正常换行
                        print(f"\n{line}")

            # 获取返回码
            return_code = process.poll()
            print("\n" + "=" * 60)

            result = type("Result", (), {"returncode": return_code, "stdout": "", "stderr": ""})()

            if result.returncode == 0 and os.path.isfile(output_path):
                print(f"✅ 成功处理文件: {file_name}")
                self.logger.info(f"成功处理文件: {file_path}")
                file_size = os.path.getsize(file_path)
                final_original_path = file_path

                if self.config.get("move_processed_files") and self.config.get(
                    "processed_files_destination"
                ):
                    try:
                        dest_dir = self.config["processed_files_destination"]
                        os.makedirs(dest_dir, exist_ok=True)
                        dest_path = os.path.join(dest_dir, Path(file_path).name)
                        if os.path.exists(dest_path):
                            raise FileExistsError(f"目标文件已存在: {dest_path}")
                        final_original_path = shutil.move(file_path, dest_path)
                        print(f"📁 文件已移动到: {final_original_path}")
                        self.logger.info(f"文件已移动到: {final_original_path}")
                    except Exception as e:
                        print(f"❌ 移动文件失败: {e}")
                        self.logger.error(f"移动文件失败 {file_path}: {e}")

                with self.processed_files_lock:
                    self.processed_files[file_hash] = {
                        "original_path": final_original_path,
                        "output_path": output_path,
                        "processed_time": datetime.now().isoformat(),
                        "file_size": file_size,
                    }
                    self._save_processed_files()
                with self.queue_lock:
                    self.processing_progress[file_path] = 100

                # 发送处理完成事件
                self._emit_web_event(
                    "processing_completed",
                    {
                        "file": file_name,
                        "path": final_original_path,
                        "output_path": output_path,
                        "time": datetime.now().isoformat(),
                    },
                )

            else:
                missing_output = result.returncode == 0
                reason = "未生成预期输出文件" if missing_output else f"返回码 {result.returncode}"
                print(f"❌ 处理失败: {file_name} ({reason})")
                self.logger.error(f"处理失败 {file_path}: {reason}")
                self._emit_web_event(
                    "processing_failed",
                    {
                        "file": file_name,
                        "path": file_path,
                        "return_code": result.returncode,
                        "message": reason,
                        "time": datetime.now().isoformat(),
                    },
                )

        except Exception as e:
            print(f"💥 处理异常: {file_name} - {e}")
            self.logger.error(f"处理异常 {file_path}: {e}")
            self._emit_web_event(
                "processing_failed",
                {
                    "file": file_name,
                    "path": file_path,
                    "message": str(e),
                    "time": datetime.now().isoformat(),
                },
            )
        finally:
            with self.active_processes_lock:
                self.active_processes.pop(file_path, None)
            with self.queue_lock:
                self.processing_files.discard(file_path)
                self.processing_progress.pop(file_path, None)
            self._emit_queue_updated()
            print("🔄 处理完成，继续处理队列中的下一个文件...")

    def process_new_file(self, file_path: str, profile_name: str | None = None):
        """处理新文件"""
        file_path = os.path.normpath(file_path)
        if profile_name:
            profiles = self.config.get("lada_profiles") or {}
            if profile_name not in profiles:
                raise ValueError(f"不存在 Lada 参数模板: {profile_name}")
            with self.queue_lock:
                self.queued_profiles[self._path_key(file_path)] = profile_name

        def discard_profile():
            with self.queue_lock:
                self.queued_profiles.pop(self._path_key(file_path), None)

        if self.should_stop:
            discard_profile()
            return

        # 检查是否是程序输出的文件，避免无限循环
        if self._is_output_file(file_path):
            self.logger.debug(f"跳过程序输出的文件，避免无限循环: {file_path}")
            discard_profile()
            return

        if not self._is_video_file(file_path):
            discard_profile()
            return

        with self.queue_lock:
            if file_path in self.processing_files or file_path in self.processing_queue:
                discard_profile()
                return

        options, _resolved_profile = self._lada_options_for_file(file_path)
        if self._has_recorded_path(file_path) or self._find_existing_output(file_path, options):
            self.logger.debug(f"文件已有处理结果，跳过: {file_path}")
            discard_profile()
            return

        # 检查文件是否已就绪
        if not self._is_file_ready(file_path):
            self._schedule_file_ready_retry(file_path)
            return

        if self.should_stop:
            discard_profile()
            return

        self._clear_file_ready_retry(file_path)

        # 检查文件是否已经处理过
        if self._is_already_processed(file_path):
            file_name = os.path.basename(file_path)
            self.logger.info(f"文件已处理过，跳过: {file_path}")
            print(f"⏭️  文件已处理过，跳过: {file_name}")
            self._clear_file_ready_retry(file_path)
            discard_profile()
            return

        if self.should_stop:
            discard_profile()
            return

        # 添加到处理队列
        added = False
        with self.queue_lock:
            if file_path not in self.processing_queue and file_path not in self.processing_files:
                self.processing_queue.append(file_path)
                added = True
                print(
                    f"📋 文件已加入处理队列: {os.path.basename(file_path)} "
                    f"(队列长度: {len(self.processing_queue)})"
                )
                self.logger.info(
                    f"文件已加入处理队列: {file_path} (队列长度: {len(self.processing_queue)})"
                )

        if added:
            self._emit_queue_updated()
            self._ensure_queue_workers()
        else:
            discard_profile()

    def submit_file(self, file_path: str, profile_name: str | None = None):
        """Schedule readiness and identity checks without blocking watchdog callbacks."""
        if self.should_stop or not self.candidate_executor:
            return
        normalized = os.path.normpath(file_path)
        key = self._path_key(normalized)
        with self.candidate_lock:
            if key in self.candidate_paths:
                return
            self.candidate_paths.add(key)

        def check_candidate():
            try:
                self.process_new_file(normalized, profile_name)
            finally:
                with self.candidate_lock:
                    self.candidate_paths.discard(key)

        try:
            self.candidate_executor.submit(check_candidate)
        except RuntimeError:
            with self.candidate_lock:
                self.candidate_paths.discard(key)

    def _ensure_queue_workers(self):
        """按配置补足队列处理线程。"""
        threads_to_start = []
        with self.queue_lock:
            if self.should_stop:
                return
            needed = min(
                len(self.processing_queue),
                max(0, self.max_concurrent_processes - self.active_workers),
            )
            for _ in range(needed):
                self.active_workers += 1
                self.is_processing = True
                worker = threading.Thread(target=self._process_queue, daemon=True)
                self.worker_threads.append(worker)
                threads_to_start.append(worker)

        for worker in threads_to_start:
            worker.start()

    def _process_queue(self):
        """处理队列中的文件"""
        try:
            while not self.should_stop:
                with self.queue_lock:
                    if not self.processing_queue:
                        break
                    file_path = self.processing_queue.pop(0)

                self._emit_queue_updated()
                self._process_video_file(file_path)
        finally:
            with self.queue_lock:
                self.active_workers = max(0, self.active_workers - 1)
                self.is_processing = self.active_workers > 0
            self._emit_queue_updated()
            self._ensure_queue_workers()

    def scan_existing_files(self):
        """扫描现有文件"""
        print("🔍 扫描现有视频文件...")
        self.logger.info("扫描现有视频文件...")

        found_files = []
        skipped_files = []
        for directory in self.config["monitor_directories"]:
            if not os.path.exists(directory):
                self.logger.warning(f"监控目录不存在: {directory}")
                print(f"❌ 监控目录不存在: {directory}")
                continue

            for root, dirs, files in os.walk(directory):
                output_dir_name = self._output_dir_name().lower()
                dirs[:] = [name for name in dirs if name.lower() != output_dir_name]
                for file in files:
                    file_path = os.path.join(root, file)
                    if self._is_video_file(file_path):
                        if self._is_output_file(file_path):
                            skipped_files.append(file_path)
                            self.logger.debug(f"跳过已处理的文件: {file_path}")
                        else:
                            found_files.append(file_path)

        if skipped_files:
            print(f"⏭️  跳过 {len(skipped_files)} 个已处理的输出文件")
            self.logger.info(f"跳过已处理的文件: {len(skipped_files)} 个")

        if found_files:
            print(f"📁 发现 {len(found_files)} 个待处理视频文件，正在加入处理队列...")
            for file_path in found_files:
                self.submit_file(file_path)
        else:
            print("📁 未发现待处理的视频文件")

    def start_monitoring(self):
        """Start monitoring while holding the cross-process instance lock."""
        if self.config.get("preflight_on_start", True) and not self._preflight_completed:
            report = self.run_preflight()
            if report["status"] == "fail":
                message = "启动前环境诊断失败；请运行 lada-monitor --diagnose 查看详情"
                self.logger.error(message)
                print(f"❌ {message}")
                self._emit_web_event("monitoring_failed", {"message": message})
                return False
        if not self.acquire_instance_lock():
            message = "另一个 LADA monitor 已在使用这份配置，拒绝重复启动"
            self.logger.error(message)
            print(f"❌ {message}")
            self._emit_web_event("monitoring_failed", {"message": message})
            return False
        try:
            self._run_monitoring()
        finally:
            self.release_instance_lock()
        return True

    def run_preflight(self) -> dict:
        """Run the same headless-safe checks used by the Web diagnostics panel."""
        report = diagnose_environment(self.config)
        self.last_preflight_report = report
        self._preflight_completed = True
        for check in report.get("checks") or []:
            log = self.logger.error if check["status"] == "fail" else self.logger.info
            log(f"启动检查 [{check['status']}] {check['label']}: {check['message']}")
        return report

    def _run_monitoring(self):
        """Run the watcher loop after the caller reserves the instance lock."""
        self.logger.info("启动LADA视频文件监控程序")
        self.logger.info(f"监控目录: {', '.join(self.config['monitor_directories'])}")
        output_dir_name = self._output_dir_name()
        configured_output = str(self.config.get("output_directory") or "").strip()
        output_description = configured_output or f"各输入目录下的 {output_dir_name} 文件夹"
        self.logger.info(f"输出目录: {output_description}")
        print(f"\n🔍 监控状态: 正在监控目录 {', '.join(self.config['monitor_directories'])}")
        print(f"📁 输出目录: {output_description}")
        print(f"⚙️  最大并发处理数: {self.config.get('max_concurrent_processes', 1)}")
        print("⏳ 等待新视频文件... (按 Ctrl+C 停止监控)")

        # 创建观察者
        self.observer = Observer()
        self.event_handler = VideoFileHandler(self)

        # 监控所有指定目录
        for directory in self.config["monitor_directories"]:
            if os.path.exists(directory):
                self.observer.schedule(self.event_handler, directory, recursive=True)
                self.logger.info(f"开始监控: {directory}")
                print(f"✅ 开始监控: {directory}")
            else:
                self.logger.warning(f"监控目录不存在: {directory}")
                print(f"❌ 监控目录不存在: {directory}")

        self.observer.start()
        self._emit_web_event("monitoring_started")
        self._start_uptime_kuma_heartbeat()

        # watcher 启动后再扫描，避免启动扫描期间落入的新文件漏掉。
        self.scan_existing_files()
        # 显示一次当前基线状态
        self._print_status()

        scan_interval = self._config_int("scan_interval", 10, 1)
        next_scan = time.monotonic() + scan_interval
        try:
            # 仅在状态发生变化时打印（由 _emit_queue_updated 触发），空闲时不刷屏
            while not self.should_stop:
                time.sleep(1)
                if time.monotonic() >= next_scan:
                    self.scan_existing_files()
                    next_scan = time.monotonic() + scan_interval

        except KeyboardInterrupt:
            self.logger.info("收到停止信号，正在停止监控...")
            print("\n🛑 收到停止信号，正在停止监控...")
            self.should_stop = True
            self._stop_uptime_kuma_heartbeat()

        self._cancel_file_ready_retries()
        self._stop_uptime_kuma_heartbeat()
        if self.observer:
            self.observer.stop()
            self.observer.join()
        print("✅ 监控已停止")


class VideoFileHandler(FileSystemEventHandler):
    """文件系统事件处理器"""

    def __init__(self, monitor: LadaMonitor):
        self.monitor = monitor

    def on_created(self, event):
        """新文件创建事件"""
        if not event.is_directory:
            self.monitor.submit_file(event.src_path)

    def on_moved(self, event):
        """文件移动事件"""
        if not event.is_directory:
            self.monitor.submit_file(event.dest_path)


def main(config_path: str | None = None) -> int:
    """主函数"""
    print("启动LADA监控程序...")

    # 配置文件、日志、状态文件的相对路径都在
    # _fix_relative_paths 中基于 config.json 所在目录解析,
    # 无需修改进程的工作目录。
    monitor = LadaMonitor(config_path)
    if monitor.config:
        print("开始监控...")
        try:
            started = monitor.start_monitoring()
        except KeyboardInterrupt:
            print("收到停止信号，正在停止监控...")
            return 0
        return 0 if started is not False else 2
    else:
        print("无法加载配置文件，请检查config.json")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
