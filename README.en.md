# LADA Agent

[简体中文](README.md) | English

LADA Agent is a local and LAN-oriented video restoration task manager. It watches one or more
directories, invokes the Lada CLI to restore newly discovered videos, and exposes command-line,
desktop, and Web interfaces for operating the queue.

The current version is `0.3.0`. Its parameter contract is aligned with the Lada CLI `0.11.0`
source and remains surface-compatible with the current `0.11.1-dev` CLI. See
[Lada compatibility](docs/LADA_COMPATIBILITY.md) for the distinction between source-contract
coverage and real GPU, encoder, and video validation.

## Features

| Capability | Description |
| --- | --- |
| Live monitoring | Watches one or more directories and detects created, moved, or recovery-scanned video files |
| Automatic processing | Waits for a file to become stable, invokes Lada, and records status and results |
| Loop and duplicate prevention | Skips previously processed files and output files produced by the Agent itself |
| Concurrency control | Limits simultaneous restoration jobs to protect GPU, CPU, and disk resources |
| Multiple interfaces | Provides a CLI daemon, Windows Tk desktop panel, and LAN Web dashboard |
| Video comparison | Filters history and supports side-by-side, swipe, fullscreen, and synchronized playback |
| Runtime protection | Enforces one monitor per configuration and supports Web tokens and persistent sessions |
| Cross-platform scripts | Includes Windows batch files and Linux/macOS shell scripts |
| Headless deployment | Monitor, Web, diagnostics, and smoke tests do not require a display server |
| Runtime verification | Checks the Lada version, devices, models, encoders, paths, and final argv before production work |
| Parameter profiles | Applies named directory or one-off profiles on top of global settings |

## Requirements

| Component | Requirement |
| --- | --- |
| Python | `3.12` or newer |
| Package manager | [uv](https://github.com/astral-sh/uv) for virtual environments and locked dependencies |
| Lada CLI | Installed separately and executable from `PATH` or an explicit configured path |
| Operating system | Windows or Linux; the Unix launcher runs on macOS, but upstream Lada support is version-dependent |
| Hardware | Depends on the Lada build; Agent `auto` mode preserves Lada's CUDA, Intel XPU, CPU, and development MPS selection |

## Install the Lada CLI

LADA Agent does **not** bundle Lada models or runtime files. After cloning this repository, install
[Lada](https://github.com/ladaapp/lada) separately and point `lada_cli_path` to an executable
`lada-cli`. Do not copy only `lada-cli.exe`: the Windows standalone build also requires
`_internal/` and the other files shipped beside the executable.

### Windows: Lada v0.11.0 for NVIDIA/CPU

NVIDIA RTX 20/30/40/50 systems should use the official v0.11.0 NVIDIA standalone build. The same
build can run on an x86-64 CPU with `--device cpu`, although CPU processing is substantially
slower. Intel Arc users should use `lada-v0.11.0_windows_intel.7z` from the release instead. Treat
the [Lada v0.11.0 release page](https://github.com/ladaapp/lada/releases/tag/v0.11.0) as the source
of truth.

GitHub splits the NVIDIA archive into two parts because of its size. Both parts must be in the
same directory. The following command keeps the roughly 2.50 GB download outside the repository
so it cannot be staged accidentally:

```powershell
$LadaDownload = Join-Path $env:TEMP "lada-v0.11.0-windows-nvidia"
New-Item -ItemType Directory -Force -Path $LadaDownload | Out-Null

gh release download v0.11.0 `
  --repo ladaapp/lada `
  --pattern "lada-v0.11.0_windows_nvidia.7z.*" `
  --dir $LadaDownload
```

Alternatively, download both assets manually from the release page:

```text
lada-v0.11.0_windows_nvidia.7z.001
lada-v0.11.0_windows_nvidia.7z.002
```

Verify the SHA256 checksums. They must exactly match the official values below:

```powershell
Get-FileHash "$LadaDownload\lada-v0.11.0_windows_nvidia.7z.001" -Algorithm SHA256
Get-FileHash "$LadaDownload\lada-v0.11.0_windows_nvidia.7z.002" -Algorithm SHA256
```

```text
861caf4bc3fb08bb4f145a0ef53172d051d39401e9f5b1c6cbab7206b32e518b  .001
472b8012f676cca0ef0eb6af9a69ba1256370dbe6c1c84740ab34d4c2650b796  .002
```

After installing [7-Zip](https://www.7-zip.org/), open or extract `.001`; 7-Zip reads `.002`
automatically from the same directory:

```powershell
$SevenZip = Join-Path $env:ProgramFiles "7-Zip\7z.exe"
& $SevenZip x `
  "$LadaDownload\lada-v0.11.0_windows_nvidia.7z.001" `
  "-o$LadaDownload\extracted"
```

Run the next commands from the LADA Agent repository root. Move any previous runtime outside the
repository as a rollback copy, then install the complete extracted `lada` directory as the
Git-ignored `cli/` directory:

```powershell
if (Test-Path .\cli) {
  Move-Item .\cli "$LadaDownload\cli-previous-backup"
}
Move-Item "$LadaDownload\extracted\lada" .\cli
```

The resulting layout must include at least:

```text
lada-agent/
├─ cli/
│  ├─ lada-cli.exe
│  ├─ lada.exe
│  └─ _internal/
├─ config/
└─ src/
```

Use a path relative to the configuration directory so the checkout remains portable:

```json
{
  "lada_cli_path": "../cli/lada-cli.exe"
}
```

Verify the version, CPU/CUDA devices, and Agent environment:

```powershell
.\cli\lada-cli.exe --version
.\cli\lada-cli.exe --list-devices
uv run lada-monitor --diagnose
```

The version should be `0.11.0`; an NVIDIA host will normally list both `cpu` and `cuda:0`.

### Headless Linux servers

Do not place the Windows standalone archive on Linux. Use an upstream-supported source install,
Flatpak, or Docker runtime. If Flatpak or Docker requires extra host arguments, create a
`lada-cli` wrapper that forwards `"$@"`, then point `lada_cli_path` to that wrapper. The sandbox
or container must see input, output, model, and temporary paths at the paths passed by the Agent.
See the [detailed usage guide](docs/usage.md) and
[upstream installation instructions](https://github.com/ladaapp/lada#installation).

## Quick start

### 1. Clone and install dependencies

```bash
git clone https://github.com/silencoo/lada-agent.git
cd lada-agent
uv sync
```

Development dependencies are not installed by default. Contributors can use:

```bash
uv sync --group dev
```

### 2. Generate a configuration

```bash
uv run lada-monitor --init-config
```

This creates `config/config.json` from
[config/config_example.json](config/config_example.json). It refuses to overwrite an existing
configuration unless `--force` is provided.

Review at least these fields:

```json
{
  "monitor_directories": ["C:\\Downloads\\Videos"],
  "output_directory": "C:\\Downloads\\Restored",
  "lada_cli_path": "../cli/lada-cli.exe",
  "max_concurrent_processes": 1,
  "lada_options": {
    "device": "auto",
    "fp16": null,
    "encoding_preset": "auto"
  }
}
```

Real configuration, logs, processing records, virtual environments, Lada runtime files, and
build output are ignored by Git and should not be published.

### 3. Run the service

```bash
# Run directory monitoring in the foreground; Ctrl+C stops it
uv run lada-monitor

# On a headless host, verify Lada, GPU/CPU, encoders, and directory permissions
uv run lada-monitor --diagnose

# Show the exact final Lada argv without executing it
uv run lada-monitor --preview /srv/videos/sample.mp4 --profile cpu-safe

# Extract a temporary sample and execute a real Lada/ffprobe smoke test
uv run lada-monitor --smoke-test /srv/videos/sample.mp4 --smoke-max-width 640 --smoke-max-fps 5

# Start a loopback-only Web dashboard
uv run lada-web --host 127.0.0.1 --port 5000

# Start the Windows Tk desktop panel
uv run lada-gui
```

Installed entry points:

| Command | Purpose |
| --- | --- |
| `lada-monitor` | Run directory monitoring and processing |
| `lada-web` | Start the Web dashboard |
| `lada-gui` | Start the Windows Tk desktop panel |

## Web and remote access

The Web server listens on `127.0.0.1:5000` by default. To expose it to a LAN, explicitly bind all
interfaces:

```bash
uv run lada-web --host 0.0.0.0 --port 5000
```

Non-loopback access requires an access token. Prefer an environment variable so the token does
not enter the configuration file:

```powershell
$env:LADA_AGENT_ACCESS_TOKEN = "replace-with-a-long-random-token"
uv run lada-web --host 0.0.0.0 --port 5000
```

Login sessions use a persistent signing key. The default key is stored in the ignored
`config/.web_session_secret`; back it up separately when moving a deployment. LAN deployments
should also use a host firewall. Across networks, use an authenticated HTTPS reverse proxy.

See the [Web UI guide](docs/README_WEB.md) for the full configuration and security model.

## Configuration overview

### Files and tasks

| Field | Description |
| --- | --- |
| `monitor_directories` | Directories to monitor |
| `output_directory` | Shared output directory; blank uses `output_directory_name` below each input directory |
| `supported_extensions` | Video extensions eligible for the queue |
| `scan_interval` | Recovery scan interval in addition to Watchdog events, in seconds |
| `max_concurrent_processes` | Maximum simultaneous restoration jobs |
| `move_processed_files` | Move original files after successful processing |
| `processed_files_destination` | Destination for moved originals |
| `output_file_keywords` | Keywords used to identify and skip generated output files |
| `temporary_file_extensions` | Download or copy extensions that must not be queued |

### Lada options

Common fields include `device`, `fp16`, `encoding_preset`, `mosaic_restoration_model`,
`mosaic_detection_model`, `max_clip_length`, and `detect_face_mosaics`. The Web and Tk settings
pages query the configured Lada CLI to discover devices, models, presets, encoders, and encoder
options dynamically.

`encoder` and `encoder_options` provide custom FFmpeg encoding and take precedence over
`encoding_preset`. `max_clip_length` is measured in frames, not seconds. The Web API accepts only
fields declared in `config_manager.LADA_OPTION_SCHEMA` and validates numeric boundaries.

`device: "auto"`, `encoding_preset: "auto"`, and `fp16: null` leave Lada's hardware-aware
defaults unchanged. `extra_cli_args` is a final advanced override layer for future Lada options.
It is parsed without a shell and cannot replace Agent-owned input, output, output-pattern, or
information commands.

`lada_profiles` stores named partial option sets. `directory_profiles` maps input directories to
profiles. The merge order is global `lada_options` → longest directory match → one-off task
profile → `extra_cli_args`.

## Headless Linux

Headless operation is supported. `lada-monitor` and `lada-web` do not import Tk and do not require
`DISPLAY` or Wayland. Start with:

```bash
uv run lada-monitor --diagnose
scripts/start_web_unix.sh
```

The Web server remains on `127.0.0.1` by default and can be reached through an SSH tunnel. For LAN
access, set `LADA_AGENT_HOST=0.0.0.0` and configure an access token. `packaging/systemd/` contains
systemd user-service examples. Because Web `auto_start=true` also starts the monitor, do not
enable the standalone monitor unit at the same time.

See the [detailed usage guide](docs/usage.md) for every field, migration guidance, and the wrapper
example.

## Launcher scripts

Windows:

```batch
scripts\start_monitor.bat
scripts\start_daemon.bat
scripts\start_web.bat
scripts\start_gui.bat
```

Linux/macOS:

```bash
scripts/start_unix.sh
scripts/start_daemon_unix.sh
scripts/start_web_unix.sh
```

Background launchers write PID and log files under `logs/`.

## Processing flow

1. Load configuration and run Lada, hardware, encoder, model, and path preflight checks.
2. Acquire the single-instance lock associated with that configuration.
3. Start Watchdog and run an initial recovery scan.
4. Wait for candidate files to stabilize and reject temporary or generated output files.
5. Check processing history and merge directory or one-off profiles before queueing.
6. Invoke Lada within the concurrency limit and persist results, logs, and history.

## Development and validation

```bash
uv sync --group dev
uv run --group dev pytest
uv run --group dev ruff check .
uv run --group dev ruff format --check .
```

The Python package uses the standard `src/` layout under `src/lada_agent/`. See
[release artifact boundaries](docs/RELEASE_ARTIFACTS.md) for what belongs in source and releases.

## Troubleshooting

| Problem | Recommendation |
| --- | --- |
| The Agent does not start | Confirm Python 3.12+ and run `uv sync` again |
| Files are not discovered | Check directory existence, read permissions, and monitor logs |
| Lada processing fails | Check the CLI path, complete runtime directory, model files, GPU driver, and console output |
| A new file remains pending | It may still be written; check ready-check settings and temporary extensions |
| Output is queued again | Ensure `output_file_keywords` includes the actual restored filename marker |
| Web returns 401 | Configure `LADA_AGENT_ACCESS_TOKEN` and verify the browser session |

For additional diagnostics, set the log level in private `config/config.json`:

```json
{
  "log_level": "DEBUG"
}
```

## Documentation

- [Detailed usage guide](docs/usage.md)
- [Web UI guide](docs/README_WEB.md)
- [Lada compatibility](docs/LADA_COMPATIBILITY.md)
- [Changelog](docs/CHANGELOG.md)
- [Release artifact boundaries](docs/RELEASE_ARTIFACTS.md)

## License

This project is licensed under the [MIT License](LICENSE). Licenses for bundled browser assets are
listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
