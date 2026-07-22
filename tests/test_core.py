import json
import logging
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

import pytest

from lada_agent import web_app
from lada_agent.config_manager import atomic_write_json, load_config, resolve_runtime_paths
from lada_agent.lada_monitor import LadaMonitor


@pytest.fixture
def web_config(tmp_path, monkeypatch):
    def write(**overrides):
        config = {
            "monitor_directories": [str(tmp_path / "input")],
            "lada_cli_path": "lada-cli.exe",
            "processed_files_log": str(tmp_path / "state" / "processed.json"),
            "log_file": str(tmp_path / "state" / "lada.log"),
            "auto_start": False,
            "max_concurrent_processes": 2,
        }
        config.update(overrides)
        path = tmp_path / "config.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        monkeypatch.setattr(web_app, "resolve_config_path", lambda: str(path))
        monkeypatch.setattr(web_app, "monitor", None)
        monkeypatch.setattr(web_app, "is_monitoring", False)
        return path

    return write


def test_web_config_update_uses_whitelist_and_bounds(web_config):
    config_path = web_config()
    client = web_app.app.test_client()
    response = client.post(
        "/api/config/update",
        json={
            "max_concurrent_processes": 999,
            "unexpected_top_level": "drop-me",
            "lada_options": {
                "max_clip_length": "0",
                "unexpected_option": "drop-me",
            },
        },
    )

    assert response.status_code == 200
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["max_concurrent_processes"] == 16
    assert saved["lada_options"]["max_clip_length"] == 1
    assert "unexpected_top_level" not in saved
    assert "unexpected_option" not in saved["lada_options"]


def test_remote_access_requires_configured_token(web_config):
    web_config(web_access_token="secret-token")
    client = web_app.app.test_client()
    denied = client.get("/api/status", environ_overrides={"REMOTE_ADDR": "192.0.2.10"})
    allowed = client.get(
        "/api/status",
        headers={"X-LADA-Token": "secret-token"},
        environ_overrides={"REMOTE_ADDR": "192.0.2.10"},
    )

    assert denied.status_code == 401
    assert allowed.status_code == 200


def test_cross_site_mutation_is_rejected(web_config):
    web_config()
    response = web_app.app.test_client().post(
        "/api/start",
        headers={"Origin": "https://attacker.example", "Sec-Fetch-Site": "cross-site"},
    )
    assert response.status_code == 403


def test_remote_socket_requires_authentication(monkeypatch):
    monkeypatch.setattr(web_app, "get_access_token", lambda: "socket-secret")
    with web_app.app.test_request_context(
        "/socket.io/",
        base_url="http://agent.example",
        environ_base={"REMOTE_ADDR": "192.0.2.20"},
    ):
        assert web_app.handle_connect() is False


def test_history_delete_mutates_current_monitor_state(web_config, monkeypatch):
    web_config()

    class FakeMonitor:
        def __init__(self):
            self.processed_files_lock = threading.RLock()
            self.processed_files = {
                "delete-me": {"original_path": "", "output_path": ""},
                "completed-concurrently": {
                    "original_path": "new.mp4",
                    "output_path": "new.out.mp4",
                },
            }
            self.saved = None

        def _save_processed_files(self):
            self.saved = dict(self.processed_files)

    fake_monitor = FakeMonitor()
    monkeypatch.setattr(web_app, "monitor", fake_monitor)
    response = web_app.app.test_client().delete("/api/processed", json={"hashes": ["delete-me"]})

    assert response.status_code == 200
    assert list(fake_monitor.processed_files) == ["completed-concurrently"]
    assert fake_monitor.saved == fake_monitor.processed_files


def test_atomic_json_write_preserves_complete_document(tmp_path):
    target = tmp_path / "state" / "history.json"
    atomic_write_json(str(target), {"first": 1})
    atomic_write_json(str(target), {"second": 2})
    assert json.loads(target.read_text(encoding="utf-8")) == {"second": 2}
    assert list(target.parent.glob("*.tmp")) == []


def test_start_rejects_while_previous_monitor_has_active_work(monkeypatch):
    previous = mock.Mock()
    previous.queue_lock = threading.RLock()
    previous.active_workers = 1
    previous.processing_files = {"still-running.mp4"}
    monkeypatch.setattr(web_app, "monitor", previous)
    monkeypatch.setattr(web_app, "monitor_thread", None)
    monkeypatch.setattr(web_app, "is_monitoring", False)
    create_monitor = mock.Mock()
    monkeypatch.setattr(web_app, "create_web_monitor", create_monitor)

    with web_app.monitor_state_lock:
        success, message = web_app._start_monitor_locked()

    assert success is False
    assert "仍在完成任务" in message
    create_monitor.assert_not_called()


def test_session_secret_persists_across_loads(tmp_path, monkeypatch):
    monkeypatch.delenv("LADA_AGENT_SECRET_KEY", raising=False)
    secret_path = tmp_path / ".web_session_secret"

    first_secret = web_app.get_session_secret(str(secret_path))
    second_secret = web_app.get_session_secret(str(secret_path))

    assert first_secret == second_secret
    assert len(first_secret) >= 32


def test_processed_library_and_media_use_paths_relative_to_config(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    media_dir = tmp_path / "media"
    config_dir.mkdir()
    data_dir.mkdir()
    media_dir.mkdir()

    original = media_dir / "sample.mp4"
    restored = media_dir / "sample.restored.mp4"
    original.write_bytes(b"original-video")
    restored.write_bytes(b"restored-video")

    history = {
        "sample-hash": {
            "original_path": str(original),
            "output_path": str(restored),
            "processed_time": "2026-07-14T12:00:00",
            "file_size": original.stat().st_size,
        }
    }
    (data_dir / "processed_files.json").write_text(json.dumps(history), encoding="utf-8")
    config_path = config_dir / "config.json"
    config_path.write_text(
        json.dumps({"processed_files_log": "../data/processed_files.json"}),
        encoding="utf-8",
    )

    monkeypatch.setattr(web_app, "resolve_config_path", lambda: str(config_path))
    monkeypatch.setattr(web_app, "monitor", None)
    client = web_app.app.test_client()

    library_response = client.get("/api/processed")
    assert library_response.status_code == 200
    files = library_response.get_json()["files"]
    assert len(files) == 1
    assert files[0]["name"] == "sample.mp4"
    assert files[0]["original_available"] is True
    assert files[0]["output_available"] is True

    media_response = client.get(
        files[0]["original_url"],
        headers={"Range": "bytes=2-5"},
    )
    assert media_response.status_code == 206
    assert media_response.data == b"igin"
    assert media_response.headers["Content-Range"] == "bytes 2-5/14"
    assert media_response.cache_control.private is True
    assert media_response.cache_control.max_age == 3600
    media_response.close()


def test_status_includes_library_metrics_while_monitor_is_stopped(tmp_path, monkeypatch):
    original = tmp_path / "ready.mp4"
    restored = tmp_path / "ready.restored.mp4"
    original.write_bytes(b"12345")
    restored.write_bytes(b"1234567")
    history_path = tmp_path / "processed.json"
    history_path.write_text(
        json.dumps(
            {
                "ready": {
                    "original_path": str(original),
                    "output_path": str(restored),
                    "file_size": 5,
                },
                "missing": {
                    "original_path": str(tmp_path / "missing.mp4"),
                    "output_path": str(tmp_path / "missing.restored.mp4"),
                    "file_size": 11,
                },
            }
        ),
        encoding="utf-8",
    )
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"processed_files_log": str(history_path)}),
        encoding="utf-8",
    )
    monkeypatch.setattr(web_app, "resolve_config_path", lambda: str(config_path))
    monkeypatch.setattr(web_app, "monitor", None)
    monkeypatch.setattr(web_app, "is_monitoring", False)

    response = web_app.app.test_client().get("/api/status")
    metrics = response.get_json()["metrics"]
    assert response.status_code == 200
    assert metrics["total_video_bytes"] == 16
    assert metrics["history_bytes"] == 16
    assert metrics["restored_bytes"] == 7
    assert metrics["ready_count"] == 1
    assert metrics["missing_count"] == 1


def test_clean_missing_history_only_removes_records_not_files(tmp_path, monkeypatch):
    original = tmp_path / "ready.mp4"
    restored = tmp_path / "ready.restored.mp4"
    original.write_bytes(b"original")
    restored.write_bytes(b"restored")
    history_path = tmp_path / "processed.json"
    history_path.write_text(
        json.dumps(
            {
                "ready": {"original_path": str(original), "output_path": str(restored)},
                "missing": {
                    "original_path": str(tmp_path / "gone.mp4"),
                    "output_path": str(tmp_path / "gone.restored.mp4"),
                },
            }
        ),
        encoding="utf-8",
    )
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"processed_files_log": str(history_path)}),
        encoding="utf-8",
    )
    monkeypatch.setattr(web_app, "resolve_config_path", lambda: str(config_path))
    monkeypatch.setattr(web_app, "monitor", None)

    response = web_app.app.test_client().delete(
        "/api/processed",
        json={"missing_only": True},
    )
    saved = json.loads(history_path.read_text(encoding="utf-8"))
    assert response.status_code == 200
    assert response.get_json()["deleted_hashes"] == ["missing"]
    assert list(saved) == ["ready"]
    assert original.exists()
    assert restored.exists()


class LadaAgentCoreTests(unittest.TestCase):
    def close_monitor_logger(self):
        logger = logging.getLogger("LadaMonitor")
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()

    def write_config(self, directory, config):
        path = Path(directory) / "config.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        return str(path)

    def minimal_config(self, directory, **overrides):
        config = {
            "monitor_directories": [str(Path(directory) / "input")],
            "lada_cli_path": "lada-cli.exe",
            "processed_files_log": str(Path(directory) / "state" / "processed.json"),
            "log_file": str(Path(directory) / "state" / "lada.log"),
            "auto_start": False,
            "max_concurrent_processes": 2,
        }
        config.update(overrides)
        return config

    def test_config_load_merges_defaults_and_resolves_runtime_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self.write_config(
                tmp,
                self.minimal_config(
                    tmp,
                    lada_options={"device": "cpu"},
                    processed_files_log="state/processed.json",
                    log_file="state/lada.log",
                ),
            )

            config, resolved = load_config(config_path)
            self.assertEqual(os.path.abspath(config_path), resolved)
            self.assertEqual(config["lada_options"]["device"], "cpu")
            self.assertIn("mosaic_restoration_model", config["lada_options"])
            self.assertEqual(config["output_directory_name"], "Restored")
            self.assertEqual(config["file_ready_max_retries"], 30)

            resolve_runtime_paths(config, base_dir=tmp)
            self.assertEqual(config["log_file"], os.path.join(tmp, "state", "lada.log"))
            self.assertEqual(
                config["processed_files_log"], os.path.join(tmp, "state", "processed.json")
            )

    def test_output_file_detection_uses_configured_output_directory_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self.write_config(
                tmp, self.minimal_config(tmp, output_directory_name="Cleaned")
            )
            monitor = LadaMonitor(config_path)
            self.addCleanup(self.close_monitor_logger)

            output_path = os.path.join(tmp, "CLEANED", "video.mp4")
            restored_path = os.path.join(tmp, "Restored", "video.mp4")
            keyword_path = os.path.join(tmp, "movie.RESTORED.mp4")
            self.assertTrue(monitor._is_output_file(output_path))
            self.assertFalse(monitor._is_output_file(restored_path))
            self.assertTrue(monitor._is_output_file(keyword_path))
            self.assertFalse(monitor._is_output_file(os.path.join(tmp, "movie.mp4")))
            self.close_monitor_logger()

    def test_output_directory_and_pattern_determine_real_output_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "exports"
            config_path = self.write_config(
                tmp,
                self.minimal_config(
                    tmp,
                    output_directory=str(output_dir),
                    lada_options={"output_file_pattern": "{orig_file_name}.clean.mp4"},
                ),
            )
            monitor = LadaMonitor(config_path)
            self.addCleanup(self.close_monitor_logger)

            output = monitor._output_path_for_file(str(Path(tmp) / "input" / "movie.mkv"))
            command = monitor._build_lada_command("movie.mkv", output)

            self.assertEqual(output, str(output_dir / "movie.clean.mp4"))
            self.assertNotIn("--output-file-pattern", command)
            self.close_monitor_logger()

    def test_file_hash_is_reused_while_stat_signature_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "video.mp4"
            video.write_bytes(b"video-content")
            config_path = self.write_config(tmp, self.minimal_config(tmp))
            monitor = LadaMonitor(config_path)
            self.addCleanup(self.close_monitor_logger)

            real_md5 = __import__("hashlib").md5
            with mock.patch("lada_agent.lada_monitor.hashlib.md5", wraps=real_md5) as md5:
                first = monitor._get_file_hash(str(video))
                second = monitor._get_file_hash(str(video))

            self.assertEqual(first, second)
            self.assertEqual(md5.call_count, 1)
            self.close_monitor_logger()

    def test_recorded_path_skips_readiness_and_hash_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_dir = Path(tmp) / "input"
            input_dir.mkdir()
            video = input_dir / "movie.mp4"
            output = input_dir / "Restored" / "movie.restored.mp4"
            output.parent.mkdir()
            video.write_bytes(b"video")
            output.write_bytes(b"restored")
            config_path = self.write_config(tmp, self.minimal_config(tmp))
            monitor = LadaMonitor(config_path)
            self.addCleanup(self.close_monitor_logger)
            monitor.processed_files = {
                "known": {
                    "original_path": str(video),
                    "output_path": str(output),
                    "file_size": video.stat().st_size,
                }
            }

            with (
                mock.patch.object(monitor, "_is_file_ready") as ready,
                mock.patch.object(monitor, "_get_file_hash") as file_hash,
            ):
                monitor.process_new_file(str(video))

            ready.assert_not_called()
            file_hash.assert_not_called()
            self.close_monitor_logger()

    def test_success_requires_output_and_moved_path_is_persisted(self):
        class FakeProcess:
            def __init__(self):
                self.stdout = self

            def readline(self):
                return ""

            def poll(self):
                return 0

        with tempfile.TemporaryDirectory() as tmp:
            input_dir = Path(tmp) / "input"
            destination = Path(tmp) / "archive"
            input_dir.mkdir()
            video = input_dir / "movie.mp4"
            video.write_bytes(b"video")
            config_path = self.write_config(
                tmp,
                self.minimal_config(
                    tmp,
                    move_processed_files=True,
                    processed_files_destination=str(destination),
                ),
            )
            monitor = LadaMonitor(config_path)
            self.addCleanup(self.close_monitor_logger)
            output = Path(monitor._output_path_for_file(str(video)))

            def successful_process(*_args, **_kwargs):
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(b"restored")
                return FakeProcess()

            with mock.patch(
                "lada_agent.lada_monitor.subprocess.Popen", side_effect=successful_process
            ):
                monitor._process_video_file(str(video))

            record = next(iter(monitor.processed_files.values()))
            self.assertEqual(record["original_path"], str(destination / "movie.mp4"))
            self.assertTrue(Path(record["original_path"]).is_file())
            self.assertEqual(record["output_path"], str(output))
            self.close_monitor_logger()

    def test_zero_exit_without_expected_output_is_not_recorded(self):
        class FakeProcess:
            stdout = None

            def __init__(self):
                self.stdout = self

            def readline(self):
                return ""

            def poll(self):
                return 0

        with tempfile.TemporaryDirectory() as tmp:
            input_dir = Path(tmp) / "input"
            input_dir.mkdir()
            video = input_dir / "movie.mp4"
            video.write_bytes(b"video")
            config_path = self.write_config(tmp, self.minimal_config(tmp))
            monitor = LadaMonitor(config_path)
            self.addCleanup(self.close_monitor_logger)

            with mock.patch("lada_agent.lada_monitor.subprocess.Popen", return_value=FakeProcess()):
                monitor._process_video_file(str(video))

            self.assertEqual(monitor.processed_files, {})
            self.close_monitor_logger()

    def test_unready_file_is_retried_and_then_queued(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_dir = Path(tmp) / "input"
            input_dir.mkdir()
            video_path = input_dir / "movie.mp4"
            video_path.write_bytes(b"video")
            config_path = self.write_config(
                tmp,
                self.minimal_config(
                    tmp,
                    file_ready_retry_delay=3600,
                    file_ready_max_retries=2,
                ),
            )
            monitor = LadaMonitor(config_path)
            self.addCleanup(self.close_monitor_logger)
            self.addCleanup(monitor._cancel_file_ready_retries)
            ready_states = [False, True]

            monitor._is_file_ready = lambda _path: ready_states.pop(0)
            monitor._is_already_processed = lambda _path: False
            monitor._ensure_queue_workers = lambda: None

            monitor.process_new_file(str(video_path))
            key = monitor._path_key(str(video_path))
            with monitor.file_retry_lock:
                timer = monitor.file_retry_timers.pop(key)
                timer.cancel()
                self.assertEqual(monitor.file_retry_counts[key], 1)

            monitor._retry_file_ready(str(video_path), key)

            self.assertIn(os.path.normpath(str(video_path)), monitor.processing_queue)
            with monitor.file_retry_lock:
                self.assertNotIn(key, monitor.file_retry_counts)
            self.close_monitor_logger()

    def test_existing_output_match_uses_input_stem_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_dir = Path(tmp) / "input"
            output_dir = input_dir / "Restored"
            output_dir.mkdir(parents=True)
            video_path = input_dir / "ABC-1.mp4"
            video_path.write_bytes(b"video")
            (output_dir / "ABC-10_demosaic.mp4").write_bytes(b"other")
            config_path = self.write_config(tmp, self.minimal_config(tmp))
            monitor = LadaMonitor(config_path)
            self.addCleanup(self.close_monitor_logger)

            self.assertFalse(monitor._is_already_processed(str(video_path)))

            (output_dir / "ABC-1_demosaic.mp4").write_bytes(b"processed")
            self.assertTrue(monitor._is_already_processed(str(video_path)))
            self.close_monitor_logger()

    def test_queue_workers_respect_max_concurrency(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self.write_config(
                tmp, self.minimal_config(tmp, max_concurrent_processes=2)
            )
            monitor = LadaMonitor(config_path)
            self.addCleanup(self.close_monitor_logger)
            current = 0
            max_seen = 0
            lock = threading.Lock()

            def fake_process(_path):
                nonlocal current, max_seen
                with lock:
                    current += 1
                    max_seen = max(max_seen, current)
                time.sleep(0.08)
                with lock:
                    current -= 1

            monitor._process_video_file = fake_process
            with monitor.queue_lock:
                monitor.processing_queue.extend([f"file-{i}.mp4" for i in range(5)])

            monitor._ensure_queue_workers()
            for worker in list(monitor.worker_threads):
                worker.join(timeout=2)

            self.assertEqual(max_seen, 2)
            self.assertEqual(monitor.processing_queue, [])
            self.assertEqual(monitor.active_workers, 0)
            self.close_monitor_logger()

    def test_uptime_kuma_url_merges_query_parameters(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self.write_config(tmp, self.minimal_config(tmp))
            monitor = LadaMonitor(config_path)
            self.addCleanup(self.close_monitor_logger)

            url = monitor._build_uptime_kuma_url(
                "https://uptime.example.test/api/push/key?foo=bar",
                {"status": "up", "msg": "queue ok"},
            )
            parsed = urlparse(url)
            query = parse_qs(parsed.query)
            self.assertEqual(query["foo"], ["bar"])
            self.assertEqual(query["status"], ["up"])
            self.assertEqual(query["msg"], ["queue ok"])
            self.close_monitor_logger()


if __name__ == "__main__":
    unittest.main()
