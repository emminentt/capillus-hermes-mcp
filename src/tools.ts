import { z } from "zod";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import type { CallToolResult } from "@modelcontextprotocol/sdk/types.js";
import type { AppConfig } from "./config.js";
import { PROTOCOL_NOTES } from "./constants.js";
import { CapillusStore, summarizeStreak } from "./store.js";
import { ageSeconds } from "./time.js";

export const TOOL_NAMES = [
  "capillus_status",
  "capillus_today",
  "capillus_sessions",
  "capillus_adherence",
  "capillus_observations",
  "capillus_device"
] as const;

export function registerTools(server: McpServer, config: AppConfig): void {
  const store = new CapillusStore(config);

  server.registerTool(
    "capillus_status",
    {
      title: "Capillus Status",
      description: "Current Capillus cap presence and latest treatment state.",
      inputSchema: {}
    },
    async () => {
      const state = store.readState();
      return asJson({
        provenance: "local_capillus_ble_monitor",
        present: Boolean(state.present),
        last_seen_at: state.last_seen_at ?? null,
        last_seen_age_seconds: ageSeconds(state.last_seen_at),
        current_session_id: state.current_session_id ?? null,
        current_session_start_at: state.current_session_start_at ?? null,
        current_session_age_seconds: ageSeconds(state.current_session_start_at),
        last_session_completed_at: state.last_session_completed_at ?? null,
        latest_device: state.latest_device ?? null,
        paths: store.paths()
      });
    }
  );

  server.registerTool(
    "capillus_today",
    {
      title: "Capillus Today",
      description: "Treatment completion status for the local day.",
      inputSchema: {}
    },
    async () => {
      const sessions = store.todaySessions();
      const completed = sessions.filter((session) => Boolean(session.completed));
      const state = store.readState();
      return asJson({
        provenance: "local_capillus_ble_monitor",
        time_zone: config.timeZone,
        daily_goal: config.dailyGoal,
        present: Boolean(state.present),
        completed_count: completed.length,
        goal_met: completed.length >= config.dailyGoal,
        active_session: state.current_session_id
          ? {
              id: state.current_session_id,
              start_at: state.current_session_start_at ?? null,
              age_seconds: ageSeconds(state.current_session_start_at)
            }
          : null,
        sessions
      });
    }
  );

  server.registerTool(
    "capillus_sessions",
    {
      title: "Capillus Sessions",
      description: "Recent inferred Capillus treatment sessions.",
      inputSchema: {
        days: z.number().int().min(1).max(366).default(30),
        limit: z.number().int().min(1).max(500).default(100),
        include_incomplete: z.boolean().default(true)
      }
    },
    async ({ days, limit, include_incomplete }) => {
      return asJson({
        provenance: "local_capillus_ble_monitor",
        days,
        sessions: store.sessions(days, limit, include_incomplete)
      });
    }
  );

  server.registerTool(
    "capillus_adherence",
    {
      title: "Capillus Adherence",
      description: "Daily Capillus adherence summary and current streak.",
      inputSchema: {
        days: z.number().int().min(1).max(366).default(30)
      }
    },
    async ({ days }) => {
      const adherence = store.adherence(days);
      return asJson({
        provenance: "local_capillus_ble_monitor",
        time_zone: config.timeZone,
        daily_goal: config.dailyGoal,
        ...summarizeStreak(adherence),
        days: adherence
      });
    }
  );

  server.registerTool(
    "capillus_observations",
    {
      title: "Capillus Observations",
      description: "Recent matched Capillus BLE observations and optional nearby candidates.",
      inputSchema: {
        limit: z.number().int().min(1).max(200).default(20),
        include_candidates: z.boolean().default(false)
      }
    },
    async ({ limit, include_candidates }) => {
      return asJson({
        provenance: "local_capillus_ble_monitor",
        observations: store.recentObservations(limit),
        candidates: include_candidates ? store.recentCandidates(limit) : undefined
      });
    }
  );

  server.registerTool(
    "capillus_device",
    {
      title: "Capillus Device",
      description: "Pinned device identity and observed BLE protocol notes.",
      inputSchema: {}
    },
    async () => {
      const state = store.readState();
      return asJson({
        provenance: "local_capillus_ble_monitor",
        latest_device: state.latest_device ?? null,
        protocol_notes: PROTOCOL_NOTES,
        setup_hint:
          "Run the included Python monitor from a macOS GUI session, turn the cap on, and let it auto-pin a device named like Capillus_CAP or configured by address/name/manufacturer key."
      });
    }
  );
}

function asJson(data: unknown): CallToolResult {
  return {
    content: [
      {
        type: "text",
        text: JSON.stringify(data, null, 2)
      }
    ]
  };
}
