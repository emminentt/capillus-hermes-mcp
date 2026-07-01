#!/usr/bin/env python3
"""Local Capillus BLE presence and treatment tracker."""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import logging
import os
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_CONFIG: dict[str, Any] = {
    "device": {
        "address": None,
        "name_contains": ["capillus", "curallux", "spectrum", "laser", "cap"],
        "service_uuids": [],
        "manufacturer_data_keys": [],
    },
    "scan": {
        "interval_seconds": 60,
        "timeout_seconds": 12,
        "present_rssi_min": -95,
        "stale_after_seconds": 180,
        "log_unmatched": True,
        "unmatched_rssi_min": -75,
    },
    "session": {
        "expected_seconds": 360,
        "min_complete_seconds": 360,
        "complete_grace_seconds": 45,
        "max_complete_seconds": 900,
        "daily_goal": 1,
    },
    "notifications": {
        "enabled": True,
        "notify_on_connect": True,
        "notify_on_complete": True,
        "notify_on_missing_after_hour": 21,
    },
    "storage": {
        "data_dir": "data",
        "sqlite_path": "data/capillus_monitor.sqlite3",
        "observations_jsonl": "data/observations.jsonl",
        "candidates_jsonl": "data/candidates.jsonl",
        "state_path": "data/state.json",
        "log_path": "data/capillus_monitor.log",
    },
    "openbrain": {
        "enabled": False,
        "repo_path": "/path/to/open-brain",
        "tenant": "default",
        "sync_interval_seconds": 300,
        "missing_after_hour": 21,
        "time_zone": "America/New_York",
        "sync_state_path": "data/openbrain_sync_state.json",
        "log_path": "data/capillus_openbrain_sync.log",
        "notify_on_missing": True,
    },
}


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def iso(ts: dt.datetime | None = None) -> str:
    return (ts or utcnow()).isoformat()


def parse_time(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    return deep_merge(DEFAULT_CONFIG, json.loads(path.read_text()))


def resolve_path(config_path: Path, value: str) -> Path:
    p = Path(value).expanduser()
    if p.is_absolute():
        return p
    return config_path.parent / p


def setup_logging(config: dict[str, Any], config_path: Path, verbose: bool = False) -> None:
    log_path = resolve_path(config_path, config["storage"]["log_path"])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_path), logging.StreamHandler(sys.stdout)],
    )


def mac_notify(title: str, body: str) -> None:
    script = (
        "display notification "
        + json.dumps(body)
        + " with title "
        + json.dumps(title)
    )
    try:
        subprocess.run(["osascript", "-e", script], check=False, timeout=5)
    except Exception:
        logging.getLogger("notify").debug("macOS notification failed", exc_info=True)


@dataclass
class BleObservation:
    at: str
    address: str
    name: str
    rssi: int | None
    service_uuids: list[str]
    manufacturer_data_keys: list[str]
    matched: bool


@dataclass
class CompletionDecision:
    completed: bool
    observed_duration_seconds: float
    inferred_duration_seconds: float
    completion_basis: str


def completion_decision(session_cfg: dict[str, Any], observed_duration: float) -> CompletionDecision:
    expected = float(session_cfg.get("expected_seconds", 360))
    min_complete = float(session_cfg.get("min_complete_seconds", expected))
    complete_grace = max(0.0, float(session_cfg.get("complete_grace_seconds", 0)))
    max_complete = float(session_cfg.get("max_complete_seconds", 900))
    duration = max(0.0, observed_duration)

    if duration > max_complete:
        return CompletionDecision(False, duration, duration, "out_of_range_long_window")
    if duration >= min_complete:
        return CompletionDecision(True, duration, duration, "observed_full_window")
    if duration >= max(0.0, min_complete - complete_grace):
        return CompletionDecision(True, duration, max(expected, min_complete), "inferred_cap_power_cycle")
    return CompletionDecision(False, duration, duration, "incomplete_short_window")


class Store:
    def __init__(self, config: dict[str, Any], config_path: Path) -> None:
        self.config = config
        self.config_path = config_path
        storage = config["storage"]
        self.data_dir = resolve_path(config_path, storage["data_dir"])
        self.sqlite_path = resolve_path(config_path, storage["sqlite_path"])
        self.observations_jsonl = resolve_path(config_path, storage["observations_jsonl"])
        self.candidates_jsonl = resolve_path(config_path, storage["candidates_jsonl"])
        self.state_path = resolve_path(config_path, storage["state_path"])
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.sqlite_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    at TEXT NOT NULL,
                    address TEXT NOT NULL,
                    name TEXT,
                    rssi INTEGER,
                    service_uuids TEXT NOT NULL,
                    manufacturer_data_keys TEXT NOT NULL,
                    matched INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    start_at TEXT NOT NULL,
                    end_at TEXT,
                    duration_seconds REAL,
                    observed_duration_seconds REAL,
                    inferred_duration_seconds REAL,
                    completion_basis TEXT,
                    completed INTEGER NOT NULL DEFAULT 0,
                    address TEXT,
                    name TEXT
                )
                """
            )
            self._ensure_column(conn, "sessions", "observed_duration_seconds", "REAL")
            self._ensure_column(conn, "sessions", "inferred_duration_seconds", "REAL")
            self._ensure_column(conn, "sessions", "completion_basis", "TEXT")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_observations_at ON observations(at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_observations_address ON observations(address)"
            )

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {
                "present": False,
                "last_seen_at": None,
                "current_session_id": None,
                "current_session_start_at": None,
                "last_session_completed_at": None,
                "latest_device": None,
            }
        return json.loads(self.state_path.read_text())

    def save_state(self, state: dict[str, Any]) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2) + "\n")
        tmp.replace(self.state_path)

    def record_observation(self, obs: BleObservation) -> None:
        row = {
            "at": obs.at,
            "address": obs.address,
            "name": obs.name,
            "rssi": obs.rssi,
            "service_uuids": obs.service_uuids,
            "manufacturer_data_keys": obs.manufacturer_data_keys,
            "matched": obs.matched,
        }
        target = self.observations_jsonl if obs.matched else self.candidates_jsonl
        with target.open("a") as f:
            f.write(json.dumps(row) + "\n")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO observations
                (at, address, name, rssi, service_uuids, manufacturer_data_keys, matched)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    obs.at,
                    obs.address,
                    obs.name,
                    obs.rssi,
                    json.dumps(obs.service_uuids),
                    json.dumps(obs.manufacturer_data_keys),
                    1 if obs.matched else 0,
                ),
            )

    def start_session(self, obs: BleObservation) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO sessions (start_at, address, name) VALUES (?, ?, ?)",
                (obs.at, obs.address, obs.name),
            )
            return int(cur.lastrowid)

    def end_session(self, session_id: int, end_at: str, decision: CompletionDecision) -> None:
        with self._connect() as conn:
            row = conn.execute("SELECT start_at FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if not row:
                return
            conn.execute(
                """
                UPDATE sessions
                SET end_at = ?,
                    duration_seconds = ?,
                    observed_duration_seconds = ?,
                    inferred_duration_seconds = ?,
                    completion_basis = ?,
                    completed = ?
                WHERE id = ?
                """,
                (
                    end_at,
                    decision.observed_duration_seconds,
                    decision.observed_duration_seconds,
                    decision.inferred_duration_seconds,
                    decision.completion_basis,
                    1 if decision.completed else 0,
                    session_id,
                ),
            )

    def recalculate_sessions(self) -> int:
        session_cfg = self.config["session"]
        updated = 0
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, start_at, end_at
                FROM sessions
                WHERE end_at IS NOT NULL
                """
            ).fetchall()
            for row in rows:
                start = parse_time(row["start_at"])
                end = parse_time(row["end_at"])
                if not start or not end:
                    continue
                decision = completion_decision(session_cfg, (end - start).total_seconds())
                conn.execute(
                    """
                    UPDATE sessions
                    SET duration_seconds = ?,
                        observed_duration_seconds = ?,
                        inferred_duration_seconds = ?,
                        completion_basis = ?,
                        completed = ?
                    WHERE id = ?
                    """,
                    (
                        decision.observed_duration_seconds,
                        decision.observed_duration_seconds,
                        decision.inferred_duration_seconds,
                        decision.completion_basis,
                        1 if decision.completed else 0,
                        row["id"],
                    ),
                )
                updated += 1
        return updated

    def latest_completed_session(self) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute(
                """
                SELECT *
                FROM sessions
                WHERE completed = 1 AND end_at IS NOT NULL
                ORDER BY end_at DESC
                LIMIT 1
                """
            ).fetchone()

    def sessions_today(self) -> list[sqlite3.Row]:
        now = utcnow()
        start = dt.datetime(now.year, now.month, now.day, tzinfo=dt.UTC).isoformat()
        with self._connect() as conn:
            return list(
                conn.execute(
                    """
                    SELECT * FROM sessions
                    WHERE start_at >= ?
                    ORDER BY start_at DESC
                    """,
                    (start,),
                )
            )


class CapillusMonitor:
    def __init__(self, config: dict[str, Any], config_path: Path) -> None:
        self.config = config
        self.config_path = config_path
        self.store = Store(config, config_path)
        self.log = logging.getLogger("capillus")

    def matches(self, row: dict[str, Any]) -> bool:
        device = self.config["device"]
        address = str(row.get("address") or "")
        name = str(row.get("name") or "").lower()
        service_uuids = {str(x).lower() for x in row.get("service_uuids") or []}
        manufacturer_keys = {str(x) for x in row.get("manufacturer_data_keys") or []}

        configured_address = device.get("address")
        if configured_address and address.lower() == str(configured_address).lower():
            return True

        for expected in device.get("name_contains") or []:
            expected_s = str(expected).lower()
            if expected_s and expected_s in name:
                return True

        for uuid in device.get("service_uuids") or []:
            if str(uuid).lower() in service_uuids:
                return True

        for key in device.get("manufacturer_data_keys") or []:
            if str(key) in manufacturer_keys:
                return True

        return False

    async def scan_once(self) -> list[BleObservation]:
        from bleak import BleakScanner

        scan = self.config["scan"]
        devices = await BleakScanner.discover(
            timeout=float(scan["timeout_seconds"]),
            return_adv=True,
        )
        observations: list[BleObservation] = []
        now = iso()
        for _, pair in devices.items():
            dev, adv = pair
            name = dev.name or adv.local_name or ""
            row = {
                "address": dev.address,
                "name": name,
                "rssi": getattr(adv, "rssi", None),
                "service_uuids": list(getattr(adv, "service_uuids", []) or []),
                "manufacturer_data_keys": [
                    str(k) for k in (getattr(adv, "manufacturer_data", {}) or {}).keys()
                ],
            }
            matched = self.matches(row)
            obs = BleObservation(
                at=now,
                address=row["address"],
                name=row["name"],
                rssi=row["rssi"],
                service_uuids=row["service_uuids"],
                manufacturer_data_keys=row["manufacturer_data_keys"],
                matched=matched,
            )
            observations.append(obs)
        return observations

    def should_log_candidate(self, obs: BleObservation) -> bool:
        if obs.matched:
            return True
        scan = self.config["scan"]
        if not scan.get("log_unmatched", True):
            return False
        rssi = obs.rssi if obs.rssi is not None else -999
        return rssi >= int(scan.get("unmatched_rssi_min", -75)) or bool(obs.name)

    def update_presence(self, observations: list[BleObservation]) -> None:
        state = self.store.load_state()
        scan = self.config["scan"]
        session_cfg = self.config["session"]
        notifications = self.config["notifications"]

        matched = [
            obs
            for obs in observations
            if obs.matched
            and (obs.rssi is None or obs.rssi >= int(scan.get("present_rssi_min", -95)))
        ]
        matched.sort(key=lambda obs: obs.rssi if obs.rssi is not None else -999, reverse=True)

        now = utcnow()
        was_present = bool(state.get("present"))

        if matched:
            obs = matched[0]
            state["present"] = True
            state["last_seen_at"] = obs.at
            state["latest_device"] = {
                "address": obs.address,
                "name": obs.name,
                "rssi": obs.rssi,
                "service_uuids": obs.service_uuids,
                "manufacturer_data_keys": obs.manufacturer_data_keys,
            }
            if not state.get("current_session_id"):
                session_id = self.store.start_session(obs)
                state["current_session_id"] = session_id
                state["current_session_start_at"] = obs.at
                self.log.info("Capillus candidate/session started: %s %s", obs.name, obs.address)
                if notifications.get("enabled") and notifications.get("notify_on_connect"):
                    mac_notify(
                        "Capillus detected",
                        f"{obs.name or obs.address} is visible over Bluetooth.",
                    )
        else:
            last_seen = parse_time(state.get("last_seen_at"))
            stale_after = float(scan.get("stale_after_seconds", 180))
            stale = not last_seen or (now - last_seen).total_seconds() >= stale_after
            if was_present and stale:
                session_id = state.get("current_session_id")
                start = parse_time(state.get("current_session_start_at"))
                duration = (last_seen - start).total_seconds() if last_seen and start else 0.0
                decision = completion_decision(session_cfg, duration)
                if session_id:
                    self.store.end_session(int(session_id), iso(last_seen or now), decision)
                self.log.info(
                    "Capillus candidate/session ended: observed=%.0fs inferred=%.0fs completed=%s basis=%s",
                    decision.observed_duration_seconds,
                    decision.inferred_duration_seconds,
                    decision.completed,
                    decision.completion_basis,
                )
                if decision.completed:
                    state["last_session_completed_at"] = iso(last_seen or now)
                    if notifications.get("enabled") and notifications.get("notify_on_complete"):
                        mac_notify(
                            "Capillus session logged",
                            f"Treatment session: {decision.inferred_duration_seconds / 60:.1f} min.",
                        )
                state["present"] = False
                state["current_session_id"] = None
                state["current_session_start_at"] = None

        self.store.save_state(state)

    async def loop_forever(self) -> None:
        interval = float(self.config["scan"]["interval_seconds"])
        self.log.info("Starting Capillus BLE monitor")
        while True:
            started = time.monotonic()
            try:
                observations = await self.scan_once()
                for obs in observations:
                    if self.should_log_candidate(obs):
                        self.store.record_observation(obs)
                self.update_presence(observations)
                self.log.info(
                    "BLE scan complete: %d devices, %d matched",
                    len(observations),
                    sum(1 for obs in observations if obs.matched),
                )
            except Exception:
                self.log.exception("BLE scan loop error; will retry")
            elapsed = time.monotonic() - started
            await asyncio.sleep(max(5.0, interval - elapsed))


def command_init_config(args: argparse.Namespace) -> int:
    path = Path(args.config).expanduser().resolve()
    if path.exists() and not args.force:
        raise FileExistsError(f"Refusing to overwrite existing config: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(DEFAULT_CONFIG, indent=2) + "\n")
    print(path)
    return 0


def command_run(args: argparse.Namespace) -> int:
    config_path = Path(args.config).expanduser().resolve()
    config = load_config(config_path)
    setup_logging(config, config_path, args.verbose)
    monitor = CapillusMonitor(config, config_path)
    asyncio.run(monitor.loop_forever())
    return 0


def command_scan(args: argparse.Namespace) -> int:
    config_path = Path(args.config).expanduser().resolve()
    config = load_config(config_path)
    setup_logging(config, config_path, args.verbose)
    monitor = CapillusMonitor(config, config_path)
    observations = asyncio.run(monitor.scan_once())
    for obs in sorted(
        observations,
        key=lambda item: (not item.matched, -(item.rssi if item.rssi is not None else -999)),
    ):
        print(json.dumps(obs.__dict__))
    return 0


def command_status(args: argparse.Namespace) -> int:
    config_path = Path(args.config).expanduser().resolve()
    config = load_config(config_path)
    store = Store(config, config_path)
    state = store.load_state()
    sessions = [
        dict(row)
        for row in store.sessions_today()
    ]
    print(json.dumps({"state": state, "sessions_today": sessions}, indent=2))
    return 0


def command_recalculate_sessions(args: argparse.Namespace) -> int:
    config_path = Path(args.config).expanduser().resolve()
    config = load_config(config_path)
    store = Store(config, config_path)
    updated = store.recalculate_sessions()
    latest = store.latest_completed_session()
    if latest:
        state = store.load_state()
        state["last_session_completed_at"] = latest["end_at"]
        store.save_state(state)
    print(json.dumps({"updated_sessions": updated}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Capillus BLE monitor")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    init_config = sub.add_parser("init-config")
    init_config.add_argument("--force", action="store_true")
    init_config.set_defaults(func=command_init_config)

    run = sub.add_parser("run")
    run.set_defaults(func=command_run)

    scan = sub.add_parser("scan")
    scan.set_defaults(func=command_scan)

    status = sub.add_parser("status")
    status.set_defaults(func=command_status)

    recalculate = sub.add_parser("recalculate-sessions")
    recalculate.set_defaults(func=command_recalculate_sessions)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        logging.exception("Command failed")
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
