import json
import logging
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

import pytest

from lada_agent import diagnostics, web_app
from lada_agent.config_manager import (
    atomic_write_json,
    load_config,
    resolve_runtime_paths,
    sanitize_config_update,
    save_config,
)
from lada_agent.lada_client import (
    build_lada_command,
    build_lada_command_preview,
    discover_lada_capabilities,
    discover_lada_encoder_options,
    evaluate_lada_version,
    parse_extra_cli_args,
    validate_extra_cli_args,
    validate_output_file_pattern,
)
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


def test_lada_auto_defaults_preserve_upstream_hardware_detection():
    command = build_lada_command(
        "lada-cli.exe",
        "input.mp4",
        "output.mp4",
        {
            "device": "auto",
            "fp16": None,
            "encoding_preset": "auto",
            "output_file_pattern": "{orig_file_name}.restored.mp4",
        },
    )

    assert "--device" not in command
    assert "--fp16" not in command
    assert "--no-fp16" not in command
    assert "--encoding-preset" not in command
    assert "--no-mp4-fast-start" in command
    assert "--no-detect-face-mosaics" in command
    assert command[1:5] == ["--input", "input.mp4", "--output", "output.mp4"]


def test_lada_version_compatibility_is_explicit_about_validation_level():
    stable = evaluate_lada_version("Lada: 0.11.0")
    development = evaluate_lada_version("Lada: 0.11.1-dev+abc123")
    future = evaluate_lada_version("Lada: 0.12.0")

    assert stable["status"] == "source-verified"
    assert development["status"] == "compatible-unverified"
    assert future["status"] == "future-unverified"
    assert stable["latest_stable"] == "0.11.0"


def test_command_preview_reports_advanced_flags_that_override_form_values():
    preview = build_lada_command_preview(
        "lada-cli",
        "/input/movie.mp4",
        "/output/movie.restored.mp4",
        {
            "device": "cuda:0",
            "fp16": True,
            "encoding_preset": "auto",
            "output_file_pattern": "{orig_file_name}.restored.mp4",
            "extra_cli_args": "--device cpu --no-fp16",
        },
        platform="posix",
    )

    assert preview["shell"] is False
    assert preview["argv"][-3:] == ["--device", "cpu", "--no-fp16"]
    assert preview["overrides"] == ["--device", "--no-fp16"]
    assert "--device cpu" in preview["command"]


def test_named_profiles_are_sanitized_and_directory_assignments_are_validated(tmp_path):
    config = sanitize_config_update(
        {
            "lada_profiles": {"cpu-safe": {"device": "cpu", "fp16": False, "unknown": "ignored"}},
            "directory_profiles": {str(tmp_path / "input"): "cpu-safe"},
        }
    )
    config_path = tmp_path / "config.json"
    save_config(str(config_path), config)

    assert config["lada_profiles"]["cpu-safe"] == {"device": "cpu", "fp16": False}
    assert config["directory_profiles"] == {str(tmp_path / "input"): "cpu-safe"}


def test_environment_diagnostics_pass_in_headless_linux_shape(tmp_path, monkeypatch):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    cli_path = tmp_path / "lada-cli"
    cli_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    cli_path.chmod(0o755)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(
        diagnostics,
        "discover_lada_capabilities",
        lambda *_args, **_kwargs: {
            "ok": True,
            "compatibility": {
                "status": "source-verified",
                "version": "0.11.0",
                "message": "verified",
            },
            "capabilities": {
                "devices": {"ok": True, "values": ["cpu"]},
                "encoders": {"ok": True, "values": ["libx264"]},
            },
        },
    )
    monkeypatch.setattr(diagnostics.shutil, "which", lambda name: f"/usr/bin/{name}")

    report = diagnostics.diagnose_environment(
        {
            "monitor_directories": [str(input_dir)],
            "output_directory": str(tmp_path / "output"),
            "lada_cli_path": str(cli_path),
            "lada_options": {"temporary_directory": str(tmp_path)},
        }
    )

    assert report["headless"] is True
    assert report["status"] == "pass", report["checks"]
    assert {check["id"] for check in report["checks"]} >= {
        "headless",
        "lada-cli",
        "compute-device",
        "encoders",
        "ffmpeg",
    }


def test_smoke_test_uses_short_sample_and_validates_output(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    monkeypatch.setattr(diagnostics, "resolve_lada_executable", lambda _value: "lada-cli")
    monkeypatch.setattr(diagnostics.shutil, "which", lambda name: name)

    def make_sample(_ffmpeg, _source, sample_path, seconds, max_width, max_fps):
        assert seconds == 2
        assert max_width == 320
        assert max_fps == 2
        Path(sample_path).write_bytes(b"sample")

    def run_command(command, **_kwargs):
        output = command[command.index("--output") + 1]
        Path(output).write_bytes(b"restored-video")
        return mock.Mock(returncode=0, stdout="Processing audio\n")

    monkeypatch.setattr(diagnostics, "_make_short_sample", make_sample)
    monkeypatch.setattr(diagnostics, "_probe_video", lambda *_args: {"ok": True})
    monkeypatch.setattr(diagnostics.subprocess, "run", run_command)

    result = diagnostics.run_lada_smoke_test(
        {
            "lada_cli_path": "lada-cli",
            "lada_options": {"output_file_pattern": "{orig_file_name}.restored.mp4"},
        },
        str(source),
        seconds=2,
        max_width=320,
        max_fps=2,
    )

    assert result["success"] is True
    assert result["output_size"] == len(b"restored-video")
    assert result["output_path"] == ""
    assert result["sample_max_width"] == 320
    assert result["sample_max_fps"] == 2


def test_lada_explicit_options_and_advanced_overrides_are_ordered_last():
    command = build_lada_command(
        "lada-cli.exe",
        "input.mp4",
        "output.mp4",
        {
            "device": "cuda:0",
            "fp16": True,
            "mosaic_restoration_model": "basicvsrpp-v1.2",
            "max_clip_length": 240,
            "mosaic_detection_model": "v4-accurate",
            "detect_face_mosaics": True,
            "encoding_preset": "hevc-nvidia-gpu-hq",
            "mp4_fast_start": True,
            "temporary_directory": "work dir",
            "mosaic_restoration_config_path": "model.py",
            "output_file_pattern": "{orig_file_name}.restored.mp4",
            "extra_cli_args": '--device cpu --no-fp16 --encoder-options "-crf 20"',
            "encoder": "libx264",
            "encoder_options": "-crf 18",
        },
    )

    assert "--encoding-preset" not in command
    assert command[-5:] == ["--device", "cpu", "--no-fp16", "--encoder-options", "-crf 20"]
    assert command.index("--fp16") < command.index("--no-fp16")
    assert [command[command.index("--max-clip-length") + 1]] == ["240"]


def test_advanced_cli_parser_preserves_quoted_values_and_protects_task_boundaries():
    assert parse_extra_cli_args('--encoder-options "-crf 20 -preset slow" --device cpu') == [
        "--encoder-options",
        "-crf 20 -preset slow",
        "--device",
        "cpu",
    ]
    assert validate_extra_cli_args("--output elsewhere.mp4")
    assert validate_extra_cli_args("--list-devices")
    assert validate_extra_cli_args("--device cpu") == []


@pytest.mark.parametrize(
    "pattern",
    ["fixed.mp4", "{orig_file_name}", "folder/{orig_file_name}.mp4", "{unknown}.mp4"],
)
def test_output_pattern_rejects_collisions_and_unsupported_templates(pattern):
    assert validate_output_file_pattern(pattern)


def test_output_pattern_accepts_safe_per_input_name():
    assert validate_output_file_pattern("{orig_file_name}.clean.mp4") == []


def test_web_rejects_protected_advanced_arguments(web_config):
    config_path = web_config()
    response = web_app.app.test_client().post(
        "/api/config/update",
        json={"lada_options": {"extra_cli_args": "--input other.mp4"}},
    )

    assert response.status_code == 400
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert "extra_cli_args" not in saved.get("lada_options", {})


def test_web_keeps_long_custom_model_paths(web_config):
    config_path = web_config()
    model_path = "C:/models/" + ("nested/" * 30) + "custom-model.pth"
    response = web_app.app.test_client().post(
        "/api/config/update",
        json={"lada_options": {"mosaic_restoration_model": model_path}},
    )

    assert response.status_code == 200
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["lada_options"]["mosaic_restoration_model"] == model_path


def test_lada_capability_discovery_parses_cli_tables(monkeypatch):
    outputs = {
        "--version": "Lada: 0.11.0",
        "--list-devices": (
            "Available devices:\n  Device  Description\n  ------  -----------\n"
            "  cpu     CPU\n  xpu:0   Intel Arc"
        ),
        "--list-encoding-presets": "Presets:\n  Name          Description\n  h264-cpu-fast Fast",
        "--list-encoders": "Encoders:\n  Name     Description\n  libx264  H.264",
        "--list-mosaic-restoration-models": (
            "Models:\n  Name              Path\n  basicvsrpp-v1.2   model.pth"
        ),
        "--list-mosaic-detection-models": "Models:\n  Name     Path\n  v4-fast  model.pt",
    }

    def fake_run(command, **_kwargs):
        return mock.Mock(returncode=0, stdout=outputs[command[-1]])

    monkeypatch.setattr("lada_agent.lada_client.subprocess.run", fake_run)
    result = discover_lada_capabilities("lada-cli.exe")

    assert result["ok"] is True
    assert result["version"] == "Lada: 0.11.0"
    assert result["capabilities"]["devices"]["values"] == ["cpu", "xpu:0"]
    assert result["capabilities"]["restoration_models"]["values"] == ["basicvsrpp-v1.2"]


def test_lada_encoder_option_discovery_uses_required_encoder_argument(monkeypatch):
    run = mock.Mock(
        return_value=mock.Mock(returncode=0, stdout="Options:\n  Name  Type\n  crf   int")
    )
    monkeypatch.setattr("lada_agent.lada_client.subprocess.run", run)

    result = discover_lada_encoder_options("lada-cli.exe", "libx264")

    assert result["ok"] is True
    assert result["values"] == ["crf"]
    assert run.call_args.args[0] == [
        "lada-cli.exe",
        "--list-encoder-options",
        "libx264",
    ]


def test_lada_capabilities_api_uses_unsaved_cli_path(web_config, monkeypatch):
    web_config()
    probe = mock.Mock(
        return_value={
            "ok": True,
            "cli_path": "next-lada.exe",
            "version": "Lada: 0.11.0",
            "version_error": "",
            "capabilities": {},
        }
    )
    monkeypatch.setattr(web_app, "discover_lada_capabilities", probe)

    response = web_app.app.test_client().post(
        "/api/lada/capabilities", json={"lada_cli_path": "next-lada.exe"}
    )

    assert response.status_code == 200
    assert response.get_json()["success"] is True
    probe.assert_called_once_with("next-lada.exe")


def test_web_command_preview_uses_unsaved_options(web_config):
    web_config()
    response = web_app.app.test_client().post(
        "/api/lada/command-preview",
        json={
            "config": {
                "lada_cli_path": "lada-cli",
                "lada_options": {
                    "device": "cuda:0",
                    "extra_cli_args": "--device cpu",
                },
            },
            "input": "/srv/input.mp4",
            "output": "/srv/output.mp4",
        },
    )

    data = response.get_json()
    assert response.status_code == 200
    assert data["success"] is True
    assert data["argv"][-2:] == ["--device", "cpu"]
    assert data["overrides"] == ["--device"]


def test_web_diagnostics_accepts_unsaved_config(web_config, monkeypatch):
    web_config()
    diagnose = mock.Mock(return_value={"status": "pass", "checks": [], "headless": True})
    monkeypatch.setattr(web_app, "diagnose_environment", diagnose)

    response = web_app.app.test_client().post(
        "/api/lada/diagnostics",
        json={"config": {"lada_cli_path": "next-lada"}},
    )

    assert response.status_code == 200
    assert response.get_json()["headless"] is True
    assert diagnose.call_args.args[0]["lada_cli_path"] == "next-lada"


def test_manual_profile_task_is_restricted_to_monitored_files(tmp_path, monkeypatch):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    video = input_dir / "movie.mp4"
    video.write_bytes(b"video")

    candidate = mock.Mock()
    candidate.config = {
        "monitor_directories": [str(input_dir)],
        "lada_profiles": {"cpu-safe": {"device": "cpu"}},
    }
    monkeypatch.setattr(web_app, "monitor", candidate)
    monkeypatch.setattr(web_app, "is_monitoring", True)
    monkeypatch.setattr(web_app, "reconcile_monitor_state", lambda: True)

    response = web_app.app.test_client().post(
        "/api/tasks/enqueue",
        json={"path": str(video), "profile": "cpu-safe"},
    )

    assert response.status_code == 200
    candidate.submit_file.assert_called_once_with(str(video), "cpu-safe")


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


def test_start_validation_blocks_failed_preflight(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    candidate = mock.Mock()
    candidate.config = {
        "monitor_directories": [str(input_dir)],
        "lada_cli_path": sys.executable,
        "preflight_on_start": True,
    }
    candidate.run_preflight.return_value = {
        "status": "fail",
        "checks": [
            {
                "status": "fail",
                "message": "encoder unavailable",
            }
        ],
    }

    error = web_app._validate_monitor_start(candidate)

    assert "encoder unavailable" in error
    candidate.run_preflight.assert_called_once_with()


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

    def test_directory_profile_overrides_global_options_for_matching_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_dir = Path(tmp) / "input"
            nested_dir = input_dir / "cpu"
            nested_dir.mkdir(parents=True)
            config_path = self.write_config(
                tmp,
                self.minimal_config(
                    tmp,
                    monitor_directories=[str(input_dir)],
                    lada_options={"device": "cuda:0"},
                    lada_profiles={
                        "cpu-safe": {"device": "cpu", "fp16": False},
                        "one-off": {"fp16": True},
                    },
                    directory_profiles={str(nested_dir): "cpu-safe"},
                ),
            )
            monitor = LadaMonitor(config_path)
            self.addCleanup(self.close_monitor_logger)

            options, profile = monitor._lada_options_for_file(str(nested_dir / "movie.mp4"))

            self.assertEqual(profile, "cpu-safe")
            self.assertEqual(options["device"], "cpu")
            self.assertFalse(options["fp16"])

            task_path = str(nested_dir / "manual.mp4")
            monitor.queued_profiles[monitor._path_key(task_path)] = "one-off"
            options, profile = monitor._lada_options_for_file(task_path)
            self.assertEqual(profile, "cpu-safe → one-off")
            self.assertEqual(options["device"], "cpu")
            self.assertTrue(options["fp16"])
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
