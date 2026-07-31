import json
from pathlib import Path

from lada_agent import cli, web_app
from lada_agent.config_manager import resolve_config_path
from lada_agent.instance_lock import MonitorInstanceLock

ROOT = Path(__file__).resolve().parents[1]


def test_cli_can_initialize_config_without_source_checkout(tmp_path):
    config_path = tmp_path / "config" / "config.json"

    assert cli.main(["--init-config", "--config", str(config_path)]) == 0
    config = json.loads(config_path.read_text(encoding="utf-8"))

    assert config["monitor_directories"] == []
    assert config["lada_cli_path"] == "lada-cli"
    assert config["lada_options"]["max_clip_length"] == 180
    assert config["lada_options"]["device"] == "auto"
    assert config["lada_options"]["fp16"] is None
    assert config["lada_options"]["encoding_preset"] == "auto"
    assert config["lada_options"]["extra_cli_args"] == ""
    assert {".asf", ".m4v", ".mpeg", ".mpg", ".ts"} <= set(config["supported_extensions"])
    assert cli.main(["--init-config", "--config", str(config_path)]) == 1


def test_config_path_environment_override(tmp_path, monkeypatch):
    config_path = tmp_path / "isolated" / "config.json"
    monkeypatch.setenv("LADA_AGENT_CONFIG", str(config_path))
    assert resolve_config_path() == str(config_path.resolve())


def test_monitor_instance_lock_rejects_a_second_owner(tmp_path):
    path = tmp_path / ".lada-monitor.lock"
    first = MonitorInstanceLock(path)
    second = MonitorInstanceLock(path)

    assert first.acquire() is True
    assert second.acquire() is False
    first.release()
    assert second.acquire() is True
    second.release()


def test_web_ui_is_self_contained_and_keeps_video_regression_contracts():
    template = (ROOT / "src/lada_agent/templates/index.html").read_text(encoding="utf-8")
    script = (ROOT / "src/lada_agent/static/app.js").read_text(encoding="utf-8")
    vendor = ROOT / "src/lada_agent/static/vendor"

    assert "https://" not in template
    assert (vendor / "socket.io/socket.io.min.js").stat().st_size > 10_000
    assert (vendor / "remixicon/remixicon.woff2").stat().st_size > 10_000
    assert 'id="compareSlider"' in template
    assert 'id="compareFullscreenBtn"' in template
    assert 'id="compareBuffering"' in template
    assert 'id="probeLadaBtn"' in template
    assert 'id="extraCliArgs"' in template
    assert 'id="encoderOptionsHelp"' in template
    assert 'id="diagnosticsBtn"' in template
    assert 'id="commandPreview"' in template
    assert 'id="smokeTestBtn"' in template
    assert 'id="ladaProfiles"' in template
    assert 'id="manualTaskPath"' in template
    assert "probeLadaCapabilities()" in template
    assert "inspectEncoderOptions()" in template
    assert "runDiagnostics()" in template
    assert "previewCommand()" in template
    assert "startSmokeTest()" in template
    assert "currentConfig.lada_options.extra_cli_args" in script
    assert "original_available && file.output_available" in script
    assert "setVideoSource('originalVideo', 'originalUnavailable', '')" in script
    assert "setVideoSource('restoredVideo', 'restoredUnavailable', '')" in script

    client = web_app.app.test_client()
    rendered = client.get("/")
    assert rendered.status_code == 200
    assert b"https://" not in rendered.data
    assert client.get("/static/vendor/socket.io/socket.io.min.js").status_code == 200
    assert client.get("/static/vendor/remixicon/remixicon.woff2").status_code == 200


def test_unix_scripts_use_current_uv_entrypoint():
    for name in ("start_unix.sh", "start_daemon_unix.sh"):
        script = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert "requirements.txt" not in script
        assert "src/lada_monitor.py" not in script
        assert "uv run lada-monitor" in script

    web_script = (ROOT / "scripts/start_web_unix.sh").read_text(encoding="utf-8")
    assert "uv run lada-web" in web_script
    assert "LADA_AGENT_HOST" in web_script
