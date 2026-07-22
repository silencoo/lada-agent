#!/usr/bin/env python3
"""
LADA监控程序Web管理界面
提供网页管理功能，包括状态监控、队列管理、日志查看等
"""

import hmac
import json
import logging
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from urllib.parse import urlsplit

from flask import Flask, jsonify, render_template, request, send_file, session, url_for
from flask_socketio import SocketIO, emit

from lada_agent.config_manager import (
    atomic_write_json,
    coerce_bool,
    load_config,
    normalize_path,
    resolve_runtime_paths,
    sanitize_config_update,
    save_config,
)
from lada_agent.config_manager import (
    resolve_config_path as shared_resolve_config_path,
)

# 导入原有的监控程序
from lada_agent.lada_monitor import LadaMonitor


def get_session_secret(secret_path: str | None = None) -> str | bytes:
    """Return a stable Flask signing key, creating a local one when needed."""
    env_secret = os.environ.get("LADA_AGENT_SECRET_KEY")
    if env_secret:
        return env_secret

    if secret_path is None:
        config_dir = os.path.dirname(shared_resolve_config_path())
        secret_path = os.environ.get("LADA_AGENT_SECRET_KEY_FILE") or os.path.join(
            config_dir, ".web_session_secret"
        )

    try:
        if os.path.exists(secret_path):
            with open(secret_path, encoding="utf-8") as secret_file:
                saved_secret = secret_file.read().strip()
            if len(saved_secret) >= 32:
                return saved_secret

        os.makedirs(os.path.dirname(os.path.abspath(secret_path)), exist_ok=True)
        new_secret = secrets.token_urlsafe(48)
        try:
            with open(secret_path, "x", encoding="utf-8") as secret_file:
                secret_file.write(new_secret)
            try:
                os.chmod(secret_path, 0o600)
            except OSError:
                pass
            return new_secret
        except FileExistsError:
            with open(secret_path, encoding="utf-8") as secret_file:
                saved_secret = secret_file.read().strip()
            if len(saved_secret) >= 32:
                return saved_secret
    except OSError as exc:
        logging.getLogger(__name__).warning(
            "Unable to persist the web session secret at %s: %s", secret_path, exc
        )

    return os.urandom(32)


app = Flask(__name__)
app.config["SECRET_KEY"] = get_session_secret()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)
socketio = SocketIO(app)


def get_socketio_client_url():
    """Use the vendored Socket.IO client unless an operator explicitly overrides it."""
    try:
        cfg, _ = load_config(resolve_config_path())
        custom = (cfg.get("web_socketio_client_url") or "").strip()
        if custom:
            return custom
    except Exception:
        pass

    return url_for("static", filename="vendor/socket.io/socket.io.min.js")


# 配置Web访问日志（与LADA处理日志分离）
def setup_web_logging():
    """设置Web访问日志"""
    # 使用工作目录作为日志根目录,这样无论是源码运行还是 pip 安装运行都一致
    web_log_file = os.path.join(os.getcwd(), "logs", "web_access.log")

    # 确保日志目录存在
    log_dir = os.path.dirname(web_log_file)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)

    # 创建Web访问日志记录器（只记录Flask的请求）
    web_logger = logging.getLogger("werkzeug")
    web_logger.setLevel(logging.INFO)

    # 移除默认的处理器，避免重复输出
    for handler in web_logger.handlers[:]:
        web_logger.removeHandler(handler)
        handler.close()

    # 创建文件处理器（只写入文件，不输出到控制台）
    file_handler = RotatingFileHandler(
        web_log_file,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.INFO)

    # 设置日志格式
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler.setFormatter(formatter)

    web_logger.addHandler(file_handler)

    # 防止日志传播到根记录器（避免干扰其他日志）
    web_logger.propagate = False

    # 创建Flask应用日志记录器
    flask_logger = logging.getLogger("flask")
    flask_logger.setLevel(logging.INFO)
    for handler in flask_logger.handlers[:]:
        flask_logger.removeHandler(handler)
        handler.close()
    flask_logger.addHandler(file_handler)
    flask_logger.propagate = False

    return web_log_file


# 初始化Web日志
web_log_file = setup_web_logging()

# 全局监控器实例
monitor = None
monitor_thread = None
is_monitoring = False
processing_started_at = {}
processing_timing_lock = threading.RLock()
monitor_state_lock = threading.RLock()
history_lock = threading.RLock()
config_lock = threading.RLock()


def _file_size(path):
    try:
        return os.path.getsize(path) if path and os.path.isfile(path) else 0
    except OSError:
        return 0


def _processed_history_path():
    config, config_path = load_config(resolve_config_path())
    resolve_runtime_paths(config, base_dir=os.path.dirname(config_path))
    return config.get("processed_files_log", "")


def _load_processed_items():
    """Read processed history even before the monitor has been started."""
    with monitor_state_lock:
        current_monitor = monitor
        if current_monitor:
            with current_monitor.processed_files_lock:
                return list(current_monitor.processed_files.items())
    try:
        path = _processed_history_path()
        if path and os.path.exists(path):
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
                return list(data.items()) if isinstance(data, dict) else []
    except (OSError, json.JSONDecodeError):
        pass
    return []


def _save_processed_items(items):
    """Persist history changes without deleting any referenced media files."""
    data = dict(items)
    path = _processed_history_path()
    if not path:
        raise OSError("processed_files_log is not configured")
    atomic_write_json(path, data)


def _queue_metrics(queue, processing, progress):
    """Calculate byte totals and ETAs from observed progress, never fake a rate."""
    now = time.monotonic()
    active = []
    rates = []
    with processing_timing_lock:
        for path in processing:
            started = processing_started_at.get(path)
            pct = max(0.0, min(100.0, float(progress.get(path, 0) or 0)))
            size = _file_size(path)
            elapsed = max(0.0, now - started) if started else 0.0
            rate = (size * pct / 100.0 / elapsed) if size and pct > 0 and elapsed >= 2 else 0.0
            remaining = (size * (1 - pct / 100.0) / rate) if rate > 0 else None
            if rate > 0:
                rates.append(rate)
            active.append(
                {
                    "name": os.path.basename(path),
                    "path": path,
                    "size": size,
                    "percent": round(pct, 1),
                    "elapsed_seconds": round(elapsed),
                    "eta_seconds": round(remaining) if remaining is not None else None,
                }
            )

    queued = [
        {"name": os.path.basename(path), "path": path, "size": _file_size(path)} for path in queue
    ]
    processing_bytes = sum(item["size"] for item in active)
    queued_bytes = sum(item["size"] for item in queued)
    total_bytes = processing_bytes + queued_bytes
    remaining_bytes = sum(item["size"] * (1 - item["percent"] / 100.0) for item in active) + sum(
        item["size"] for item in queued
    )
    aggregate_rate = sum(rates)
    if not aggregate_rate and rates:
        aggregate_rate = sum(rates)
    total_eta = round(remaining_bytes / aggregate_rate) if aggregate_rate > 0 else None
    return {
        "processing": active,
        "queue": queued,
        "queue_size": len(queued),
        "processing_bytes": processing_bytes,
        "queued_bytes": queued_bytes,
        "total_bytes": total_bytes,
        "remaining_bytes": round(remaining_bytes),
        "throughput_bytes_per_second": round(aggregate_rate),
        "eta_seconds": total_eta,
    }


def _overview_metrics(queue, processing, progress, processed_items):
    """Combine live pipeline metrics with persistent library statistics."""
    metrics = _queue_metrics(queue, processing, progress)
    known_sources = {}
    history_bytes = 0
    restored_bytes = 0
    ready_count = 0
    partial_count = 0
    missing_count = 0

    for _, info in processed_items:
        if not isinstance(info, dict):
            continue
        original_path = info.get("original_path", "")
        output_path = info.get("output_path", "")
        original_available = bool(original_path and os.path.isfile(original_path))
        output_available = bool(output_path and os.path.isfile(output_path))
        source_size = int(info.get("file_size", 0) or _file_size(original_path))
        history_bytes += source_size
        restored_bytes += _file_size(output_path)
        if original_path:
            known_sources[os.path.normcase(os.path.normpath(original_path))] = source_size

        if original_available and output_available:
            ready_count += 1
        elif original_available or output_available:
            partial_count += 1
        else:
            missing_count += 1

    for item in metrics["processing"] + metrics["queue"]:
        path = item.get("path", "")
        if path:
            known_sources[os.path.normcase(os.path.normpath(path))] = item.get("size", 0)

    metrics.update(
        {
            "total_video_bytes": sum(known_sources.values()),
            "history_bytes": history_bytes,
            "restored_bytes": restored_bytes,
            "ready_count": ready_count,
            "partial_count": partial_count,
            "missing_count": missing_count,
            "history_count": ready_count + partial_count + missing_count,
        }
    )
    return metrics


def resolve_config_path() -> str:
    """定位配置文件路径"""
    return shared_resolve_config_path()


def _is_loopback_peer() -> bool:
    remote_addr = request.remote_addr or ""
    return remote_addr in {"127.0.0.1", "::1", "localhost"} or remote_addr.startswith("::ffff:127.")


def is_local_request() -> bool:
    """Treat only a loopback peer using a loopback Host header as local."""
    host = urlsplit(f"//{request.host}").hostname or ""
    return _is_loopback_peer() and host.lower() in {"127.0.0.1", "::1", "localhost"}


def get_access_token() -> str:
    """Return the configured remote access token, if any."""
    env_token = (
        os.environ.get("LADA_AGENT_ACCESS_TOKEN") or os.environ.get("LADA_AGENT_TOKEN") or ""
    ).strip()
    if env_token:
        return env_token
    try:
        cfg, _ = load_config(resolve_config_path())
        return (cfg.get("web_access_token") or "").strip()
    except Exception:
        return ""


def get_request_token() -> str:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return (request.headers.get("X-LADA-Token") or request.form.get("token") or "").strip()


def is_trusted_mutation_origin() -> bool:
    """Reject browser requests sent from another site, including localhost CSRF."""
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return True
    if request.headers.get("Sec-Fetch-Site", "").lower() == "cross-site":
        return False
    origin = request.headers.get("Origin")
    if not origin:
        return True
    supplied = urlsplit(origin)
    expected_scheme = request.scheme
    expected_host = request.host
    if _is_loopback_peer():
        expected_scheme = (
            request.headers.get("X-Forwarded-Proto", expected_scheme).split(",")[0].strip()
        )
        expected_host = request.headers.get("X-Forwarded-Host", expected_host).split(",")[0].strip()
    return (supplied.scheme, supplied.netloc) == (expected_scheme, expected_host)


def wants_json_response() -> bool:
    return (
        request.path.startswith("/api/")
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
    )


@app.before_request
def enforce_access_control():
    """Local requests are allowed; remote requests need a configured token."""
    if not is_trusted_mutation_origin():
        return jsonify({"success": False, "message": "拒绝跨站请求"}), 403
    if request.endpoint in {"static", "login", "logout"}:
        return None
    if is_local_request():
        return None

    expected_token = get_access_token()
    if not expected_token:
        return jsonify(
            {
                "success": False,
                "message": (
                    "远程访问未启用。请设置 LADA_AGENT_ACCESS_TOKEN 或 config.web_access_token。"
                ),
            }
        ), 403

    if session.get("lada_authenticated"):
        return None

    provided_token = get_request_token()
    if provided_token and hmac.compare_digest(provided_token, expected_token):
        session["lada_authenticated"] = True
        return None

    if wants_json_response():
        return jsonify({"success": False, "message": "需要访问令牌"}), 401
    return render_template("login.html"), 401


@app.after_request
def add_security_headers(response):
    """添加轻量安全响应头，避免影响当前内联模板结构。"""
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    return response


@app.route("/login", methods=["GET", "POST"])
def login():
    if is_local_request():
        session["lada_authenticated"] = True
        return render_template("index.html", socketio_client_url=get_socketio_client_url())

    expected_token = get_access_token()
    if not expected_token:
        return jsonify({"success": False, "message": "远程访问未启用"}), 403

    if request.method == "POST":
        provided_token = get_request_token()
        if provided_token and hmac.compare_digest(provided_token, expected_token):
            session["lada_authenticated"] = True
            return render_template("index.html", socketio_client_url=get_socketio_client_url())
        return render_template("login.html", error="访问令牌无效"), 401

    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.pop("lada_authenticated", None)
    return jsonify({"success": True})


def open_path_in_os(path: str):
    """在本机打开目录或文件"""
    if not path:
        return False, "路径为空"

    normalized = normalize_path(path)
    if not os.path.exists(normalized):
        return False, f"路径不存在: {normalized}"

    try:
        if os.name == "nt":
            os.startfile(normalized)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", normalized])
        else:
            opener = shutil.which("xdg-open")
            if opener:
                subprocess.Popen([opener, normalized])
            else:
                return False, "无法找到打开方式 (xdg-open)"
        return True, "已打开"
    except Exception as e:
        return False, f"打开失败: {str(e)}"


def reconcile_monitor_state():
    """校准监控线程状态"""
    global is_monitoring
    with monitor_state_lock:
        if not is_monitoring:
            return False
        if not monitor or not monitor_thread or not monitor_thread.is_alive():
            is_monitoring = False
        return is_monitoring


def _monitor_has_active_work(candidate) -> bool:
    if not candidate:
        return False
    with candidate.queue_lock:
        return bool(candidate.active_workers or candidate.processing_files)


def _validate_monitor_start(candidate) -> str | None:
    if not candidate.config:
        return "无法加载配置文件"
    directories = candidate.config.get("monitor_directories") or []
    if not directories:
        return "请先设置监控目录"
    if not any(os.path.isdir(path) for path in directories):
        return "配置的监控目录均不存在"

    lada_path = (candidate.config.get("lada_cli_path") or "").strip()
    if not lada_path:
        return "LADA CLI 路径未配置"
    if os.path.isabs(lada_path) and not os.path.exists(lada_path):
        return f"LADA CLI 路径不存在: {lada_path}"
    if (
        not os.path.isabs(lada_path)
        and not os.path.exists(lada_path)
        and not shutil.which(lada_path)
    ):
        return f"无法在当前目录或PATH中找到 LADA CLI: {lada_path}"
    return None


def _start_monitor_locked():
    global monitor, monitor_thread, is_monitoring
    reconcile_monitor_state()
    if is_monitoring:
        return False, "监控已在运行中"
    if _monitor_has_active_work(monitor):
        return False, "上一监控器仍在完成任务，请等待或强制停止"

    candidate = create_web_monitor()
    error = _validate_monitor_start(candidate)
    if error:
        return False, error
    if not candidate.acquire_instance_lock():
        return False, "另一个 LADA monitor 已在使用这份配置"
    monitor = candidate
    monitor_thread = threading.Thread(target=monitor.start_monitoring, daemon=True)
    try:
        monitor_thread.start()
    except Exception:
        monitor.release_instance_lock()
        raise
    is_monitoring = True
    return True, "监控已启动"


def restart_monitor_with_config():
    """重启监控器以应用最新配置"""
    global monitor, monitor_thread, is_monitoring
    with monitor_state_lock:
        if monitor:
            monitor.stop_monitoring(terminate_running=True)
        is_monitoring = False
        if monitor_thread and monitor_thread.is_alive():
            monitor_thread.join(timeout=20)
        if monitor_thread and monitor_thread.is_alive():
            raise RuntimeError("旧监控线程未能停止，拒绝启动重叠实例")
        if _monitor_has_active_work(monitor):
            raise RuntimeError("旧监控任务未能停止，拒绝启动重叠实例")
        candidate = create_web_monitor()
        error = _validate_monitor_start(candidate)
        if error:
            monitor = candidate
            monitor_thread = None
            raise RuntimeError(error)
        if not candidate.acquire_instance_lock():
            monitor = candidate
            monitor_thread = None
            raise RuntimeError("另一个 LADA monitor 已在使用这份配置")
        monitor = candidate
        monitor_thread = threading.Thread(target=monitor.start_monitoring, daemon=True)
        try:
            monitor_thread.start()
        except Exception:
            monitor.release_instance_lock()
            raise
        is_monitoring = True


def create_web_monitor():
    """创建支持Web的监控器"""
    monitor = LadaMonitor()

    # 设置Web回调
    def emit_web_event(event, data=None):
        socketio.emit(event, data or {})

    def processing_started(data):
        with processing_timing_lock:
            processing_started_at[data.get("path", "")] = time.monotonic()
        emit_web_event("processing_started", data)

    def processing_finished(event, data):
        with processing_timing_lock:
            processing_started_at.pop(data.get("path", ""), None)
        emit_web_event(event, data)

    monitor.set_web_callback("processing_started", processing_started)
    monitor.set_web_callback(
        "processing_completed", lambda data: processing_finished("processing_completed", data)
    )
    monitor.set_web_callback(
        "processing_failed", lambda data: processing_finished("processing_failed", data)
    )
    monitor.set_web_callback(
        "processing_progress", lambda data: emit_web_event("processing_progress", data)
    )
    monitor.set_web_callback("queue_updated", lambda data: emit_web_event("queue_updated", data))
    monitor.set_web_callback(
        "monitoring_started", lambda data: emit_web_event("monitoring_started", data)
    )
    monitor.set_web_callback(
        "monitoring_stopped", lambda data: emit_web_event("monitoring_stopped", data)
    )
    monitor.set_web_callback(
        "monitoring_failed", lambda data: emit_web_event("monitoring_failed", data)
    )

    return monitor


def get_monitor_status():
    """获取监控器状态"""
    with monitor_state_lock:
        reconcile_monitor_state()
        current_monitor = monitor
        running = is_monitoring
    processed_items = _load_processed_items()
    if not current_monitor:
        return {
            "is_running": False,
            "queue_size": 0,
            "processing_files": [],
            "processed_count": len(processed_items),
            "metrics": _overview_metrics([], [], {}, processed_items),
            "config": None,
        }

    with current_monitor.queue_lock:
        queue = list(current_monitor.processing_queue)
        processing_files = list(current_monitor.processing_files)
        progress_by_path = dict(getattr(current_monitor, "processing_progress", {}))
    return {
        "is_running": running,
        "queue_size": len(queue),
        "processing_files": processing_files,
        "processing_progress": {
            os.path.basename(path): pct for path, pct in progress_by_path.items()
        },
        "processed_count": len(processed_items),
        "metrics": _overview_metrics(queue, processing_files, progress_by_path, processed_items),
        "config": {
            "monitor_directories": current_monitor.config.get("monitor_directories", []),
            "output_directory": current_monitor.config.get("output_directory", ""),
            "max_concurrent_processes": current_monitor.config.get("max_concurrent_processes", 1),
        },
    }


@app.route("/")
def index():
    """主页"""
    return render_template("index.html", socketio_client_url=get_socketio_client_url())


@app.route("/api/status")
def api_status():
    """获取状态API"""
    return jsonify(get_monitor_status())


@app.route("/api/start", methods=["POST"])
def api_start():
    """启动监控API"""
    global monitor, monitor_thread, is_monitoring

    try:
        with monitor_state_lock:
            success, message = _start_monitor_locked()
        return jsonify({"success": success, "message": message}), 200 if success else 409
    except Exception as e:
        return jsonify({"success": False, "message": f"启动失败: {str(e)}"})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    """停止监控API"""
    global is_monitoring

    try:
        data = request.get_json(silent=True) or {}
        terminate_running = (
            coerce_bool(data.get("terminate_running")) if isinstance(data, dict) else False
        )
        with monitor_state_lock:
            reconcile_monitor_state()
            if not is_monitoring or not monitor:
                return jsonify({"success": False, "message": "监控未在运行"}), 409
            monitor.stop_monitoring(terminate_running=terminate_running)
            is_monitoring = False
        return jsonify({"success": True, "message": "监控已停止"})
    except Exception as e:
        return jsonify({"success": False, "message": f"停止失败: {str(e)}"})


@app.route("/api/queue")
def api_queue():
    """获取队列信息API"""
    if not monitor:
        return jsonify(_overview_metrics([], [], {}, _load_processed_items()))

    with monitor.queue_lock:
        queue = list(monitor.processing_queue)
        processing = list(monitor.processing_files)
        progress = dict(getattr(monitor, "processing_progress", {}))

    metrics = _overview_metrics(queue, processing, progress, _load_processed_items())
    metrics["processing_progress"] = {os.path.basename(path): pct for path, pct in progress.items()}
    return jsonify(metrics)


@app.route("/api/logs")
def api_logs():
    """获取日志API"""
    log_type = request.args.get("type", "lada")  # 默认为LADA处理日志

    logs = []

    try:
        if log_type == "web":
            # 返回Web访问日志
            log_file = web_log_file
        else:
            # 返回LADA处理日志
            if not monitor or not monitor.config:
                return jsonify({"logs": [], "type": log_type})
            log_file = monitor.config.get("log_file", "lada_monitor.log")

        if os.path.exists(log_file):
            with open(log_file, encoding="utf-8") as f:
                lines = f.readlines()
                # 返回最后100行日志
                logs = [line.strip() for line in lines[-100:]]
        else:
            logs = [f"日志文件不存在: {log_file}"]
    except Exception as e:
        logs = [f"读取日志失败: {str(e)}"]

    return jsonify({"logs": logs, "type": log_type})


@app.route("/api/processed")
def api_processed():
    """获取已处理文件列表API"""
    files = []
    for file_hash, info in _load_processed_items():
        if not isinstance(info, dict):
            continue
        original_path = info.get("original_path", "")
        output_path = info.get("output_path", "")
        files.append(
            {
                "hash": file_hash,
                "name": os.path.basename(original_path),
                "original_path": original_path,
                "output_path": output_path,
                "processed_time": info.get("processed_time", ""),
                "file_size": info.get("file_size", 0) or _file_size(original_path),
                "output_size": _file_size(output_path),
                "original_available": bool(original_path and os.path.isfile(original_path)),
                "output_available": bool(output_path and os.path.isfile(output_path)),
                "original_url": url_for("api_media", file_hash=file_hash, kind="original"),
                "output_url": url_for("api_media", file_hash=file_hash, kind="restored"),
            }
        )

    # 按处理时间倒序排列
    files.sort(key=lambda x: x["processed_time"], reverse=True)

    return jsonify({"files": files})


@app.route("/api/processed", methods=["DELETE"])
def api_delete_processed():
    """Delete selected or fully-missing history records, never media files."""
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"success": False, "message": "请求体必须是JSON对象"}), 400

    requested_hashes = payload.get("hashes", [])
    if not isinstance(requested_hashes, list):
        return jsonify({"success": False, "message": "hashes必须是数组"}), 400
    requested = {str(value) for value in requested_hashes[:5000] if value is not None}
    missing_only = bool(payload.get("missing_only", False))
    try:
        with history_lock:
            with monitor_state_lock:
                current_monitor = monitor
                if current_monitor:
                    with current_monitor.processed_files_lock:
                        items = list(current_monitor.processed_files.items())
                        targets = _history_delete_targets(items, requested, missing_only)
                        if not targets:
                            return jsonify(
                                {"success": False, "message": "没有选择要清理的历史记录"}
                            ), 400
                        deleted = [
                            str(key)
                            for key in current_monitor.processed_files
                            if str(key) in targets
                        ]
                        for key in list(current_monitor.processed_files):
                            if str(key) in targets:
                                del current_monitor.processed_files[key]
                        if deleted:
                            current_monitor._save_processed_files()
                else:
                    items = _load_processed_items()
                    targets = _history_delete_targets(items, requested, missing_only)
                    if not targets:
                        return jsonify(
                            {"success": False, "message": "没有选择要清理的历史记录"}
                        ), 400
                    remaining = [(key, info) for key, info in items if str(key) not in targets]
                    deleted = [str(key) for key, _ in items if str(key) in targets]
                    if deleted:
                        _save_processed_items(remaining)
    except OSError as error:
        return jsonify({"success": False, "message": f"保存历史记录失败: {error}"}), 500
    return jsonify({"success": True, "deleted": len(deleted), "deleted_hashes": deleted})


def _history_delete_targets(items, requested, missing_only):
    targets = set(requested)
    if missing_only:
        for file_hash, info in items:
            if not isinstance(info, dict):
                continue
            original_path = info.get("original_path", "")
            output_path = info.get("output_path", "")
            if not os.path.isfile(original_path) and not os.path.isfile(output_path):
                targets.add(str(file_hash))
    return targets


@app.route("/api/media/<file_hash>/<kind>")
def api_media(file_hash, kind):
    """Stream only files referenced by the processed-history allowlist."""
    if kind not in {"original", "restored"}:
        return jsonify({"success": False, "message": "无效的媒体类型"}), 400
    for known_hash, info in _load_processed_items():
        if not hmac.compare_digest(str(known_hash), str(file_hash)) or not isinstance(info, dict):
            continue
        path = info.get("original_path" if kind == "original" else "output_path", "")
        if not path or not os.path.isfile(path):
            return jsonify({"success": False, "message": "视频文件不存在"}), 404
        response = send_file(path, conditional=True)
        response.cache_control.no_cache = False
        response.cache_control.public = False
        response.cache_control.private = True
        response.cache_control.max_age = 3600
        return response
    return jsonify({"success": False, "message": "未找到处理记录"}), 404


@app.route("/api/config")
def api_get_config():
    """获取当前配置API"""
    if not monitor or not monitor.config:
        config, _ = load_config()
    else:
        config = monitor.config

    return jsonify(
        {
            "monitor_directories": config.get("monitor_directories", []),
            "output_directory": config.get("output_directory", ""),
            "scan_interval": config.get("scan_interval", 10),
            "max_concurrent_processes": config.get("max_concurrent_processes", 1),
            "auto_start": config.get("auto_start", False),
            "lada_cli_path": config.get("lada_cli_path", "lada-cli.exe"),
            "lada_options": config.get("lada_options", {}),
            "uptime_kuma": config.get("uptime_kuma", {}),
        }
    )


@app.route("/api/config/update", methods=["POST"])
def api_update_config():
    """更新配置API"""
    global monitor, monitor_thread
    try:
        reconcile_monitor_state()
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"success": False, "message": "请求体必须是JSON对象"}), 400

        config_path = resolve_config_path()
        with config_lock:
            existing_config, _ = load_config(config_path)
            full_config = sanitize_config_update(data, existing_config)
            save_config(config_path, full_config)

        with monitor_state_lock:
            running = reconcile_monitor_state()
        if running:
            restart_monitor_with_config()
            return jsonify({"success": True, "message": "配置已更新，监控已重启"})

        with monitor_state_lock:
            monitor = None
            monitor_thread = None

        return jsonify({"success": True, "message": "配置已更新"})
    except Exception as e:
        return jsonify({"success": False, "message": f"更新配置失败: {str(e)}"})


@app.route("/api/open_path", methods=["POST"])
def api_open_path():
    """打开指定路径"""
    try:
        if not is_local_request():
            return jsonify({"success": False, "message": "打开本机路径仅允许从服务器本机访问"}), 403
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify({"success": False, "message": "请求体必须是JSON对象"}), 400
        raw_path = data.get("path", "")
        create_if_missing = bool(data.get("create"))
        normalized = normalize_path(raw_path)

        if create_if_missing and normalized and not os.path.exists(normalized):
            os.makedirs(normalized, exist_ok=True)

        success, message = open_path_in_os(normalized)
        return jsonify({"success": success, "message": message, "path": normalized})
    except Exception as e:
        return jsonify({"success": False, "message": f"打开路径失败: {str(e)}"})


def _select_folder_via_subprocess():
    """使用子进程运行Tk对话框，避免主线程/子线程问题"""
    script = r"""
import tkinter as tk
from tkinter import filedialog

root = tk.Tk()
root.withdraw()
try:
    root.attributes('-topmost', True)
except Exception:
    pass
root.deiconify()
root.withdraw()
path = filedialog.askdirectory(title="选择监控目录")
root.destroy()
if path:
    print(path)
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Folder dialog failed")
    return result.stdout.strip()


@app.route("/api/select_folder", methods=["POST"])
def api_select_folder():
    """打开本地文件夹选择对话框"""
    try:
        if not is_local_request():
            return jsonify({"success": False, "message": "文件夹选择器仅允许从服务器本机访问"}), 403
        folder_path = _select_folder_via_subprocess()
        if folder_path:
            folder_path = os.path.normpath(folder_path)
            return jsonify({"success": True, "path": folder_path})
        return jsonify({"success": False, "message": "用户取消选择"})
    except Exception as e:
        return jsonify({"success": False, "message": f"打开文件夹选择器失败: {str(e)}"})


@socketio.on("connect")
def handle_connect(auth=None):
    """WebSocket连接处理"""
    if not is_local_request() and not session.get("lada_authenticated"):
        expected_token = get_access_token()
        provided_token = ""
        if isinstance(auth, dict):
            provided_token = str(auth.get("token") or "").strip()
        if (
            not expected_token
            or not provided_token
            or not hmac.compare_digest(provided_token, expected_token)
        ):
            return False
        session["lada_authenticated"] = True
    print("客户端已连接")
    emit("status_update", get_monitor_status())


@socketio.on("disconnect")
def handle_disconnect():
    """WebSocket断开处理"""
    print("客户端已断开")


def run_web_app(host="127.0.0.1", port=5000, debug=False):
    """运行Web应用"""
    print("🌐 启动LADA监控Web管理界面...")
    print(f"📱 访问地址: http://{host}:{port}")
    print(f"🔧 调试模式: {'开启' if debug else '关闭'}")
    print(f"📝 Web访问日志: {web_log_file}")
    print("📝 LADA处理日志: 查看配置文件中的log_file设置")
    print("=" * 60)

    should_autostart = not debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true"
    if should_autostart:
        config, _ = load_config(resolve_config_path())
        if config.get("auto_start"):
            try:
                with monitor_state_lock:
                    success, message = _start_monitor_locked()
                if not success:
                    logging.getLogger(__name__).warning("Auto-start skipped: %s", message)
            except Exception:
                logging.getLogger(__name__).exception("Unable to auto-start monitor")

    # 在非调试模式下，减少控制台日志输出
    if not debug:
        # 设置werkzeug日志级别，减少控制台输出
        werkzeug_log = logging.getLogger("werkzeug")
        # 只保留文件日志，控制台不输出请求日志
        for handler in werkzeug_log.handlers[:]:
            if isinstance(handler, logging.StreamHandler) and not isinstance(
                handler, RotatingFileHandler
            ):
                werkzeug_log.removeHandler(handler)

    socketio.run(app, host=host, port=port, debug=debug, allow_unsafe_werkzeug=True)


if __name__ == "__main__":
    run_web_app(debug=True)
