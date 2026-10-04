// Thin client for the HQ API. Every write carries the x-hq header (CSRF guard).

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "x-hq": "1" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      msg = (await res.json()).error ?? msg;
    } catch { /* not json */ }
    if (res.status === 401 && !path.startsWith("/login") && !path.startsWith("/verify")) {
      window.dispatchEvent(new Event("hq:logout"));
    }
    throw new ApiError(res.status, msg);
  }
  return res.json() as Promise<T>;
}

export const api = {
  get: <T>(p: string) => call<T>("GET", p),
  post: <T>(p: string, body?: unknown) => call<T>("POST", p, body ?? {}),
};

// ---- shapes returned by the API

export type AgentState = "ok" | "late" | "failed" | "paused" | "idle";

export interface Run {
  id: number;
  agent?: string;
  job_name: string;
  started_at: string;
  status: "ok" | "failed";
  used_fallback: number;
  fallback_model?: string | null;
  headline: string;
  body?: string;
}

export interface AgentSummary {
  id: string;
  name: string;
  runs_on: "server" | "pc";
  purpose: string;
  state: AgentState;
  jobs: number;
  next_run_at: string | null;
  last: (Run & { agent: string }) | null;
  p1: number;
  week: { n: number; failed: number; fallback: number; failed_24h: number };
}

export interface Overview {
  agents: AgentSummary[];
  ideas_week: { type: string; n: number }[];
  customers: { date: string; accounts: number; paying: number; revenue_usd: number; winning: number; at_risk: number } | null;
  timeline: (Run & { agent: string })[];
  quiet_hours: boolean;
  last_ingest: string | null;
  fleet_paused: boolean;
  fallback_24h: number;
  priority: { top: PriorityTop[]; p1: number; p2: number };
}

export interface PriorityTop {
  id: number;
  agent: string;
  date: string;
  type: string;
  title: string;
  score: number;
  tier: "P1" | "P2" | "P3";
  why: string[];
}

export interface Job {
  id: string;
  agent: string;
  name: string;
  schedule: string;
  deliver: string;
  state: string;
  model: string | null;
  no_agent: number;
  last_run_at: string | null;
  last_status: string | null;
  last_error: string | null;
  next_run_at: string | null;
  health: string;
}

export interface AgentDetail {
  id: string;
  name: string;
  runs_on: string;
  purpose: string;
  jobs: Job[];
  runs: Run[];
  soul: string;
  brain: Record<string, string[]>;
}

export interface Idea {
  id: number;
  agent: string;
  date: string;
  week: string;
  type: string;
  title: string;
  summary: string;
  detail?: string;
  source_url: string | null;
  evidence: string;
  status: "new" | "approved" | "parked" | "rejected" | "done";
  note: string | null;
  decided_at: string | null;
  run_id?: number | null;
}

export interface Prospect {
  cid: string;
  date: string;
  business: string;
  niche: string;
  city: string;
  rank: number;
  rating: number;
  reviews: number;
  claimed: string;
  photos: number;
  website: string;
  phone: string;
  email: string;
  signals: string;
  status: string;
  note: string | null;
}

// ---- formatting helpers

export function ago(iso?: string | null): string {
  if (!iso) return "never";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return iso;
  const s = (Date.now() - t) / 1000;
  const fut = s < 0;
  const a = Math.abs(s);
  const txt = a < 60 ? `${Math.round(a)}s` : a < 3600 ? `${Math.round(a / 60)}m` : a < 86400 ? `${Math.round(a / 3600)}h` : `${Math.round(a / 86400)}d`;
  return fut ? `in ${txt}` : `${txt} ago`;
}

// Next-run wording: a slot already passed means the job is due, not "ago".
export function due(iso?: string | null): string {
  if (!iso) return "never";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return iso;
  return t <= Date.now() ? "due now" : ago(iso);
}

export function clock(iso?: string | null): string {
  if (!iso) return "--:--";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}
