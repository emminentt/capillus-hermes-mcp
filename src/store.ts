import { existsSync, readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import type { AppConfig } from "./config.js";
import type { CapillusObservation, CapillusSession, CapillusState, DailyAdherence } from "./types.js";
import { dateRangeEndingToday, localDate } from "./time.js";

export class CapillusStore {
  constructor(private readonly config: AppConfig) {}

  paths(): Record<string, string> {
    return {
      monitorDataDir: this.config.monitorDataDir,
      statePath: this.config.statePath,
      sqlitePath: this.config.sqlitePath,
      observationsPath: this.config.observationsPath,
      candidatesPath: this.config.candidatesPath
    };
  }

  readState(): CapillusState {
    if (!existsSync(this.config.statePath)) {
      return {};
    }
    return JSON.parse(readFileSync(this.config.statePath, "utf8")) as CapillusState;
  }

  recentObservations(limit = 20): CapillusObservation[] {
    return readJsonl<CapillusObservation>(this.config.observationsPath).slice(-clampLimit(limit)).reverse();
  }

  recentCandidates(limit = 30): CapillusObservation[] {
    return readJsonl<CapillusObservation>(this.config.candidatesPath).slice(-clampLimit(limit)).reverse();
  }

  sessions(days = 30, limit = 100, includeIncomplete = true): CapillusSession[] {
    if (!existsSync(this.config.sqlitePath)) {
      return [];
    }
    const safeDays = Math.max(1, Math.min(366, Math.floor(days)));
    const safeLimit = clampLimit(limit, 500);
    const completedClause = includeIncomplete ? "" : "AND completed = 1";
    const sql = `
      SELECT id, start_at, end_at, duration_seconds, completed, address, name
      FROM sessions
      WHERE start_at >= datetime('now', '-${safeDays} days')
      ${completedClause}
      ORDER BY start_at DESC
      LIMIT ${safeLimit};
    `;
    return this.querySql<CapillusSession>(sql);
  }

  todaySessions(): CapillusSession[] {
    const today = localDate(new Date(), this.config.timeZone);
    return this.sessions(2, 100, true).filter((session) => localDate(session.start_at, this.config.timeZone) === today);
  }

  adherence(days = 30): DailyAdherence[] {
    const safeDays = Math.max(1, Math.min(366, Math.floor(days)));
    const sessions = this.sessions(safeDays + 1, 1000, true);
    const byDate = new Map<string, DailyAdherence>();
    for (const date of dateRangeEndingToday(safeDays, this.config.timeZone)) {
      byDate.set(date, {
        date,
        completed: 0,
        total_sessions: 0,
        goal_met: false,
        completed_seconds: 0
      });
    }
    for (const session of sessions) {
      const date = localDate(session.start_at, this.config.timeZone);
      const entry = byDate.get(date);
      if (!entry) {
        continue;
      }
      entry.total_sessions += 1;
      if (Boolean(session.completed)) {
        entry.completed += 1;
        entry.completed_seconds += Math.round(session.duration_seconds ?? this.config.expectedTreatmentSeconds);
      }
    }
    for (const entry of byDate.values()) {
      entry.goal_met = entry.completed >= this.config.dailyGoal;
    }
    return [...byDate.values()];
  }

  private querySql<T>(sql: string): T[] {
    try {
      const output = execFileSync(this.config.sqliteBin, ["-json", this.config.sqlitePath, sql], {
        encoding: "utf8",
        stdio: ["ignore", "pipe", "ignore"]
      });
      if (!output.trim()) {
        return [];
      }
      return JSON.parse(output) as T[];
    } catch {
      return [];
    }
  }
}

export function summarizeStreak(adherence: DailyAdherence[]): { current_streak_days: number; missed_days: string[] } {
  let current = 0;
  for (let i = adherence.length - 1; i >= 0; i -= 1) {
    if (!adherence[i]?.goal_met) {
      break;
    }
    current += 1;
  }
  return {
    current_streak_days: current,
    missed_days: adherence.filter((day) => !day.goal_met).map((day) => day.date)
  };
}

export function readJsonl<T>(path: string): T[] {
  if (!existsSync(path)) {
    return [];
  }
  return readFileSync(path, "utf8")
    .split(/\r?\n/)
    .filter((line) => line.trim().length > 0)
    .flatMap((line) => {
      try {
        return [JSON.parse(line) as T];
      } catch {
        return [];
      }
    });
}

function clampLimit(value: number, max = 200): number {
  return Math.max(1, Math.min(max, Math.floor(value)));
}
