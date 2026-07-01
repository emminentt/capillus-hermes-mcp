#!/usr/bin/env python3
"""Sync local Capillus adherence facts into Open Brain.

This is optional glue for people who run Open Brain locally. It reads the
Capillus monitor SQLite database, captures completed sessions once, and captures
a once-per-day missing-treatment alert after the configured local hour.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib
import json
import logging
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from capillus_monitor import load_config, mac_notify, parse_time, resolve_path


def setup_sync_logging(config: dict[str, Any], config_path: Path, verbose: bool = False) -> None:
    openbrain_cfg = config.get("openbrain") or {}
    log_path = resolve_path(config_path, openbrain_cfg.get("log_path", "data/capillus_openbrain_sync.log"))
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_path), logging.StreamHandler(sys.stdout)],
    )


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"captured_sessions": {}, "daily_missing": {}}
    try:
        state = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {"captured_sessions": {}, "daily_missing": {}}
    state.setdefault("captured_sessions", {})
    state.setdefault("daily_missing", {})
    return state


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def load_openbrain(repo_path: str):
    path = Path(repo_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Open Brain repo not found: {path}")
    sys.path.insert(0, str(path))
    return importlib.import_module("openbrain")


def capture(openbrain, tenant: str, content: str, metadata: dict[str, Any], importance: int = 7) -> dict[str, Any]:
    try:
        return openbrain.capture_thought(content, tenant=tenant, extra_meta=metadata, importance=importance)
    except TypeError:
        return openbrain.capture_thought(content, tenant=tenant)


def connect(sqlite_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row
    return conn


def session_columns(conn: sqlite3.Connection) -> set[str]:
    return {row["name"] for row in conn.execute("PRAGMA table_info(sessions)")}


def recent_sessions(sqlite_path: Path, limit: int = 200) -> list[dict[str, Any]]:
    if not sqlite_path.exists():
        return []
    with connect(sqlite_path) as conn:
        cols = session_columns(conn)
        observed = (
            "observed_duration_seconds"
            if "observed_duration_seconds" in cols
            else "duration_seconds AS observed_duration_seconds"
        )
        inferred = (
            "inferred_duration_seconds"
            if "inferred_duration_seconds" in cols
            else "duration_seconds AS inferred_duration_seconds"
        )
        inference_window = (
            "inference_window_seconds"
            if "inference_window_seconds" in cols
            else "duration_seconds AS inference_window_seconds"
        )
        close_detected = (
            "close_detected_at"
            if "close_detected_at" in cols
            else "end_at AS close_detected_at"
        )
        basis = (
            "completion_basis"
            if "completion_basis" in cols
            else "CASE WHEN completed = 1 THEN 'legacy_completed' ELSE 'legacy_incomplete' END AS completion_basis"
        )
        rows = conn.execute(
            f"""
            SELECT id, start_at, end_at, duration_seconds, {observed},
                   {inference_window}, {inferred}, {close_detected}, {basis},
                   completed, address, name
            FROM sessions
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def zone_from_config(config: dict[str, Any]) -> ZoneInfo:
    openbrain_cfg = config.get("openbrain") or {}
    zone_name = str(openbrain_cfg.get("time_zone") or "UTC")
    try:
        return ZoneInfo(zone_name)
    except Exception:
        return ZoneInfo("UTC")


def local_date(value: str | None, zone: ZoneInfo) -> str | None:
    parsed = parse_time(value)
    if not parsed:
        return None
    return parsed.astimezone(zone).date().isoformat()


def local_stamp(value: str | None, zone: ZoneInfo) -> str:
    parsed = parse_time(value)
    if not parsed:
        return value or "unknown"
    return parsed.astimezone(zone).strftime("%Y-%m-%d %H:%M:%S %Z")


def numeric(value: Any, fallback: float = 0.0) -> float:
    try:
        if value is None:
            return fallback
        return float(value)
    except (TypeError, ValueError):
        return fallback


def session_unique_key(session: dict[str, Any]) -> str:
    return f"capillus-session:{session.get('id')}:{session.get('start_at') or 'unknown-start'}"


def session_capture_text(session: dict[str, Any], zone: ZoneInfo, person_name: str, daily_rule: str) -> str:
    date = local_date(session.get("start_at"), zone) or "unknown-date"
    observed = numeric(session.get("observed_duration_seconds"), numeric(session.get("duration_seconds")))
    inference_window = numeric(session.get("inference_window_seconds"), observed)
    inferred = numeric(session.get("inferred_duration_seconds"), observed)
    basis = session.get("completion_basis") or "unknown"
    unique_key = session_unique_key(session)
    return (
        f"Capillus adherence log {unique_key} for {person_name}: on {date}, "
        f"{person_name} completed the required daily Capillus treatment. "
        f"Session id {session['id']} ran from "
        f"{local_stamp(session.get('start_at'), zone)} to {local_stamp(session.get('end_at'), zone)}. "
        f"Observed BLE window: {observed:.1f}s. Inference window: {inference_window:.1f}s. "
        f"Credited treatment duration: {inferred:.1f}s. "
        f"Completion basis: {basis}. {daily_rule}"
    )


def missing_capture_text(now_local: dt.datetime, person_name: str, daily_rule: str, daily_goal: int) -> str:
    return (
        f"Capillus adherence alert for {person_name}: as of "
        f"{now_local.strftime('%Y-%m-%d %H:%M:%S %Z')}, no completed Capillus treatment is recorded "
        f"for {now_local.date().isoformat()}. Daily goal: {daily_goal} completed session(s). "
        f"Agents should check up and remind {person_name} until the session is completed. {daily_rule}"
    )


class OpenBrainSync:
    def __init__(self, config: dict[str, Any], config_path: Path) -> None:
        self.config = config
        self.config_path = config_path
        self.openbrain_cfg = config.get("openbrain") or {}
        self.log = logging.getLogger("capillus.openbrain")
        self.sqlite_path = resolve_path(config_path, config["storage"]["sqlite_path"])
        self.state_path = resolve_path(
            config_path,
            self.openbrain_cfg.get("sync_state_path", "data/openbrain_sync_state.json"),
        )
        self.zone = zone_from_config(config)
        self.person_name = str(self.openbrain_cfg.get("person_name") or "the wearer")
        self.daily_rule = str(
            self.openbrain_cfg.get("daily_rule")
            or "Daily Capillus treatment is required and non-negotiable."
        )
        self.tenant = str(self.openbrain_cfg.get("tenant") or "default")
        self.openbrain = load_openbrain(str(self.openbrain_cfg["repo_path"]))

    def sync_once(self) -> dict[str, Any]:
        state = load_state(self.state_path)
        sessions = recent_sessions(self.sqlite_path)
        captured = 0
        today = dt.datetime.now(self.zone).date().isoformat()
        today_completed = [
            session
            for session in sessions
            if bool(session.get("completed")) and local_date(session.get("start_at"), self.zone) == today
        ]

        for session in reversed(sessions):
            session_id = str(session["id"])
            if not bool(session.get("completed")) or session_id in state["captured_sessions"]:
                continue
            content = session_capture_text(session, self.zone, self.person_name, self.daily_rule)
            metadata = {
                "source": "capillus_home_monitor",
                "capillus_session_id": session_id,
                "capillus_session_unique_key": session_unique_key(session),
                "capillus_date": local_date(session.get("start_at"), self.zone),
                "completion_basis": session.get("completion_basis"),
                "observed_duration_seconds": session.get("observed_duration_seconds"),
                "inference_window_seconds": session.get("inference_window_seconds"),
                "inferred_duration_seconds": session.get("inferred_duration_seconds"),
                "close_detected_at": session.get("close_detected_at"),
            }
            receipt = capture(self.openbrain, self.tenant, content, metadata, importance=8)
            state["captured_sessions"][session_id] = {
                "thought_id": receipt.get("id"),
                "status": receipt.get("status"),
                "captured_at": dt.datetime.now(dt.UTC).isoformat(),
            }
            captured += 1
            self.log.info("Captured Capillus session %s to Open Brain: %s", session_id, receipt.get("id"))

        now_local = dt.datetime.now(self.zone)
        missing_hour = int(self.openbrain_cfg.get("missing_after_hour", 21))
        daily_goal = int(self.config.get("session", {}).get("daily_goal", 1))
        if (
            now_local.hour >= missing_hour
            and len(today_completed) < daily_goal
            and today not in state["daily_missing"]
        ):
            content = missing_capture_text(now_local, self.person_name, self.daily_rule, daily_goal)
            metadata = {
                "source": "capillus_home_monitor",
                "capillus_date": today,
                "capillus_daily_goal": daily_goal,
                "capillus_completed_count": len(today_completed),
                "capillus_alert_type": "missing_daily_treatment",
            }
            receipt = capture(self.openbrain, self.tenant, content, metadata, importance=9)
            state["daily_missing"][today] = {
                "thought_id": receipt.get("id"),
                "status": receipt.get("status"),
                "captured_at": dt.datetime.now(dt.UTC).isoformat(),
            }
            captured += 1
            self.log.info("Captured Capillus missing-treatment alert for %s: %s", today, receipt.get("id"))
            if self.openbrain_cfg.get("notify_on_missing", True):
                mac_notify("Capillus treatment due", f"No completed Capillus session logged for {today}.")

        save_state(self.state_path, state)
        return {
            "captured": captured,
            "completed_today": len(today_completed),
            "daily_goal": daily_goal,
            "date": today,
        }

    def loop_forever(self) -> None:
        interval = max(60.0, float(self.openbrain_cfg.get("sync_interval_seconds", 300)))
        self.log.info("Starting Capillus OpenBrain sync")
        while True:
            started = time.monotonic()
            try:
                result = self.sync_once()
                self.log.info("OpenBrain sync complete: %s", json.dumps(result, sort_keys=True))
            except Exception:
                self.log.exception("OpenBrain sync error; will retry")
            elapsed = time.monotonic() - started
            time.sleep(max(30.0, interval - elapsed))


def command_once(args: argparse.Namespace) -> int:
    config_path = Path(args.config).expanduser().resolve()
    config = load_config(config_path)
    setup_sync_logging(config, config_path, args.verbose)
    if not (config.get("openbrain") or {}).get("enabled", False):
        print(json.dumps({"enabled": False}, indent=2))
        return 0
    sync = OpenBrainSync(config, config_path)
    print(json.dumps(sync.sync_once(), indent=2))
    return 0


def command_run(args: argparse.Namespace) -> int:
    config_path = Path(args.config).expanduser().resolve()
    config = load_config(config_path)
    setup_sync_logging(config, config_path, args.verbose)
    if not (config.get("openbrain") or {}).get("enabled", False):
        logging.getLogger("capillus.openbrain").info("OpenBrain sync is disabled in config")
        return 0
    OpenBrainSync(config, config_path).loop_forever()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Capillus to Open Brain sync")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    once = sub.add_parser("once")
    once.set_defaults(func=command_once)

    run = sub.add_parser("run")
    run.set_defaults(func=command_run)

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
