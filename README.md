# Capillus Hermes MCP

Local-first Bluetooth tracking and MCP tools for Bluetooth-enabled Capillus caps.

This repo has two pieces:

- `monitor/`: a Python BLE monitor that detects a Capillus cap when it powers on for treatment, logs local observations, and infers completed treatment sessions.
- `src/`: a read-only Model Context Protocol server for Hermes, Claude Desktop, or any MCP client. It reads the monitor's local files and exposes status, sessions, adherence, observations, and device identity.

No Capillus account, cloud API, private endpoint, scraping, or app automation is required.

## What It Tracks

Bluetooth-enabled Capillus caps appear only during the treatment power window. The monitor watches for a cap-like BLE advertisement, then records:

- cap present/offline state
- latest RSSI and BLE identity
- inferred treatment start/end
- completed sessions
- daily adherence and streaks

The default matcher looks for names like `Capillus_CAP`, manufacturer data keys, and optional pinned addresses. You can use your own cap by turning it on once and letting the monitor auto-detect it, then pinning the discovered identity in `monitor/config.json`.

## Install The Monitor

```bash
mkdir -p ~/.capillus-home-monitor
cp monitor/capillus_monitor.py ~/.capillus-home-monitor/
cp monitor/config.example.json ~/.capillus-home-monitor/config.json
cd ~/.capillus-home-monitor
python3 -m venv .venv
.venv/bin/pip install -r /path/to/capillus-hermes-mcp/monitor/requirements.txt
```

Run once from a GUI terminal and turn the cap on:

```bash
~/.capillus-home-monitor/.venv/bin/python ~/.capillus-home-monitor/capillus_monitor.py --config ~/.capillus-home-monitor/config.json run
```

On macOS you must grant Bluetooth permission to the Python process in System Settings. For always-on tracking, edit `monitor/deploy/launchd/com.example.capillus-monitor.plist`, replace `/Users/YOU`, copy it to `~/Library/LaunchAgents/`, and bootstrap it with `launchctl`.

## Install The MCP Server

```bash
npm install
npm run build
```

Point the MCP at your monitor data:

```bash
export CAPILLUS_MONITOR_DATA_DIR="$HOME/.capillus-home-monitor/data"
node dist/src/index.js
```

For Hermes, configure a stdio MCP server equivalent to:

```yaml
mcp_servers:
  capillus:
    enabled: true
    command: /usr/local/bin/node
    args:
      - /path/to/capillus-hermes-mcp/dist/src/index.js
    env:
      CAPILLUS_MONITOR_DATA_DIR: /Users/YOU/.capillus-home-monitor/data
      CAPILLUS_TIME_ZONE: America/New_York
```

Hermes exposes the tools with its normal `mcp_<server>_<tool>` prefix, for example `mcp_capillus_capillus_today`.

## Tools

- `capillus_status`: current presence, latest seen time, active session, device identity.
- `capillus_today`: local-day treatment completion and active session.
- `capillus_sessions`: recent inferred treatment sessions.
- `capillus_adherence`: daily adherence, missed days, and current streak.
- `capillus_observations`: recent matched BLE observations and optional nearby candidates.
- `capillus_device`: pinned identity and observed proprietary BLE service notes.

## Observed BLE Shape

The cap observed during development advertised as `Capillus_CAP`. A read-only GATT probe showed a proprietary UART-style service:

- service: `49535343-fe7d-4ae5-8fa9-9fafd205e455`
- characteristic: `49535343-1e4d-4bd9-ba61-23c647249616`
- characteristic properties: `write`, `notify`, `indicate`, `write-without-response`

The public monitor does not send control commands. It uses local Bluetooth presence and timing only.

## Safety

This is adherence telemetry, not medical advice. It does not evaluate hair growth, alter treatment, or control the cap.

