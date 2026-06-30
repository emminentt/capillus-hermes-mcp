import { existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { resolve } from "node:path";
import dotenv from "dotenv";
import { DEFAULT_DAILY_GOAL, DEFAULT_EXPECTED_SECONDS, DEFAULT_MONITOR_DIR } from "./constants.js";

export interface AppConfig {
  monitorDataDir: string;
  statePath: string;
  sqlitePath: string;
  observationsPath: string;
  candidatesPath: string;
  timeZone: string;
  sqliteBin: string;
  expectedTreatmentSeconds: number;
  dailyGoal: number;
}

export function loadEnv(): void {
  const envFile = process.env.CAPILLUS_ENV_FILE;
  if (envFile && existsSync(expandPath(envFile))) {
    dotenv.config({ path: expandPath(envFile) });
    return;
  }
  if (existsSync(resolve(process.cwd(), ".env"))) {
    dotenv.config({ path: resolve(process.cwd(), ".env") });
  }
}

export function loadConfig(): AppConfig {
  loadEnv();
  const monitorDataDir = expandPath(process.env.CAPILLUS_MONITOR_DATA_DIR ?? DEFAULT_MONITOR_DIR);
  return {
    monitorDataDir,
    statePath: expandPath(process.env.CAPILLUS_STATE_PATH ?? resolve(monitorDataDir, "state.json")),
    sqlitePath: expandPath(process.env.CAPILLUS_SQLITE_PATH ?? resolve(monitorDataDir, "capillus_monitor.sqlite3")),
    observationsPath: expandPath(
      process.env.CAPILLUS_OBSERVATIONS_PATH ?? resolve(monitorDataDir, "observations.jsonl")
    ),
    candidatesPath: expandPath(process.env.CAPILLUS_CANDIDATES_PATH ?? resolve(monitorDataDir, "candidates.jsonl")),
    timeZone: process.env.CAPILLUS_TIME_ZONE ?? Intl.DateTimeFormat().resolvedOptions().timeZone ?? "UTC",
    sqliteBin: process.env.SQLITE3_BIN ?? "sqlite3",
    expectedTreatmentSeconds: numberFromEnv("CAPILLUS_EXPECTED_TREATMENT_SECONDS", DEFAULT_EXPECTED_SECONDS),
    dailyGoal: numberFromEnv("CAPILLUS_DAILY_GOAL", DEFAULT_DAILY_GOAL)
  };
}

export function expandPath(value: string): string {
  if (value === "~") {
    return homedir();
  }
  if (value.startsWith("~/")) {
    return resolve(homedir(), value.slice(2));
  }
  return resolve(value);
}

export function readPackageVersion(): string {
  try {
    const pkg = JSON.parse(readFileSync(resolve(process.cwd(), "package.json"), "utf8")) as { version?: string };
    return pkg.version ?? "0.0.0";
  } catch {
    return "0.0.0";
  }
}

function numberFromEnv(name: string, fallback: number): number {
  const raw = process.env[name];
  if (!raw) {
    return fallback;
  }
  const parsed = Number(raw);
  return Number.isFinite(parsed) ? parsed : fallback;
}
