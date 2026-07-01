import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { execFileSync } from "node:child_process";
import { describe, expect, it } from "vitest";
import type { AppConfig } from "../src/config.js";
import { CapillusStore, summarizeStreak } from "../src/store.js";

function fixtureConfig(): AppConfig {
  const dir = mkdtempSync(join(tmpdir(), "capillus-mcp-"));
  const db = join(dir, "capillus_monitor.sqlite3");
  execFileSync("sqlite3", [
    db,
    `CREATE TABLE sessions (
      id INTEGER PRIMARY KEY,
      start_at TEXT NOT NULL,
      end_at TEXT,
      duration_seconds REAL,
      completed INTEGER NOT NULL,
      address TEXT,
      name TEXT
    );
    INSERT INTO sessions VALUES
      (1, datetime('now', '-1 day'), datetime('now', '-1 day', '+360 seconds'), 360, 1, 'abc', 'Capillus_CAP'),
      (2, datetime('now'), NULL, NULL, 0, 'abc', 'Capillus_CAP');`
  ]);
  writeFileSync(
    join(dir, "state.json"),
    JSON.stringify({
      present: true,
      last_seen_at: new Date().toISOString(),
      current_session_id: 2,
      latest_device: { address: "abc", name: "Capillus_CAP", rssi: -70 }
    })
  );
  writeFileSync(
    join(dir, "observations.jsonl"),
    JSON.stringify({ at: new Date().toISOString(), address: "abc", name: "Capillus_CAP", matched: true }) + "\n"
  );
  return {
    monitorDataDir: dir,
    statePath: join(dir, "state.json"),
    sqlitePath: db,
    observationsPath: join(dir, "observations.jsonl"),
    candidatesPath: join(dir, "candidates.jsonl"),
    timeZone: "UTC",
    sqliteBin: "sqlite3",
    expectedTreatmentSeconds: 360,
    dailyGoal: 1
  };
}

describe("CapillusStore", () => {
  it("reads state, sessions, observations, and adherence", () => {
    const store = new CapillusStore(fixtureConfig());
    expect(store.readState().present).toBe(true);
    expect(store.sessions(3, 10, true)).toHaveLength(2);
    expect(store.recentObservations(5)[0]?.name).toBe("Capillus_CAP");
    const adherence = store.adherence(3);
    expect(adherence.some((day) => day.completed === 1)).toBe(true);
    expect(summarizeStreak(adherence).missed_days.length).toBeGreaterThanOrEqual(1);
  });

  it("uses inferred treatment seconds for completed near-full BLE windows", () => {
    const config = fixtureConfig();
    execFileSync("sqlite3", [
      config.sqlitePath,
      `ALTER TABLE sessions ADD COLUMN observed_duration_seconds REAL;
       ALTER TABLE sessions ADD COLUMN inference_window_seconds REAL;
       ALTER TABLE sessions ADD COLUMN inferred_duration_seconds REAL;
       ALTER TABLE sessions ADD COLUMN close_detected_at TEXT;
       ALTER TABLE sessions ADD COLUMN completion_basis TEXT;
       UPDATE sessions
       SET duration_seconds = 333,
           observed_duration_seconds = 333,
           inference_window_seconds = 333,
           inferred_duration_seconds = 360,
           close_detected_at = end_at,
           completion_basis = 'inferred_cap_power_cycle'
       WHERE id = 1;`
    ]);
    const store = new CapillusStore(config);
    const completed = store.sessions(3, 10, false)[0];
    expect(completed?.duration_seconds).toBe(333);
    expect(completed?.inference_window_seconds).toBe(333);
    expect(completed?.treatment_seconds).toBe(360);
    expect(completed?.completion_basis).toBe("inferred_cap_power_cycle");
    expect(store.adherence(3).some((day) => day.completed_seconds === 360)).toBe(true);
  });

  it("preserves stale-close inference fields for completed cap power windows", () => {
    const config = fixtureConfig();
    execFileSync("sqlite3", [
      config.sqlitePath,
      `ALTER TABLE sessions ADD COLUMN observed_duration_seconds REAL;
       ALTER TABLE sessions ADD COLUMN inference_window_seconds REAL;
       ALTER TABLE sessions ADD COLUMN inferred_duration_seconds REAL;
       ALTER TABLE sessions ADD COLUMN close_detected_at TEXT;
       ALTER TABLE sessions ADD COLUMN completion_basis TEXT;
       UPDATE sessions
       SET duration_seconds = 293,
           observed_duration_seconds = 293,
           inference_window_seconds = 348,
           inferred_duration_seconds = 360,
           close_detected_at = datetime('now', '-1 day', '+348 seconds'),
           completion_basis = 'inferred_stale_power_window'
       WHERE id = 1;`
    ]);
    const store = new CapillusStore(config);
    const completed = store.sessions(3, 10, false)[0];
    expect(completed?.observed_duration_seconds).toBe(293);
    expect(completed?.inference_window_seconds).toBe(348);
    expect(completed?.treatment_seconds).toBe(360);
    expect(completed?.completion_basis).toBe("inferred_stale_power_window");
  });
});
