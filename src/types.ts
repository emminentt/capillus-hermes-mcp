export interface CapillusState {
  present?: boolean;
  last_seen_at?: string | null;
  current_session_id?: number | null;
  current_session_start_at?: string | null;
  last_session_completed_at?: string | null;
  latest_device?: CapillusDevice | null;
}

export interface CapillusDevice {
  address?: string | null;
  name?: string | null;
  rssi?: number | null;
  service_uuids?: string[];
  manufacturer_data_keys?: string[];
}

export interface CapillusSession {
  id: number;
  start_at: string;
  end_at: string | null;
  duration_seconds: number | null;
  observed_duration_seconds?: number | null;
  inference_window_seconds?: number | null;
  inferred_duration_seconds?: number | null;
  close_detected_at?: string | null;
  treatment_seconds?: number | null;
  completion_basis?: string | null;
  completed: number;
  address: string | null;
  name: string | null;
}

export interface CapillusObservation {
  at: string;
  address: string;
  name?: string | null;
  rssi?: number | null;
  service_uuids?: string[];
  manufacturer_data_keys?: string[];
  matched?: boolean;
}

export interface DailyAdherence {
  date: string;
  completed: number;
  total_sessions: number;
  goal_met: boolean;
  completed_seconds: number;
}
