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
});
