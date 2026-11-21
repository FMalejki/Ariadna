// Thin typed client for the FastAPI backend (/api/v1). No data is cached outside TanStack Query.

export const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const PREFIX = `${API_BASE}/api/v1`;
const TOKEN_KEY = "osint.token";
const USER_KEY = "osint.user";

export type Session = { token: string; username: string; role: "viewer" | "analyst" | "admin" };

export function loadSession(): Session | null {
  if (typeof window === "undefined") return null;
  const token = window.localStorage.getItem(TOKEN_KEY);
  const user = window.localStorage.getItem(USER_KEY);
  if (!token || !user) return null;
  try {
    return { token, ...(JSON.parse(user) as { username: string; role: Session["role"] }) };
  } catch {
    return null;
  }
}

export function saveSession(s: Session | null): void {
  if (s === null) {
    window.localStorage.removeItem(TOKEN_KEY);
    window.localStorage.removeItem(USER_KEY);
    return;
  }
  window.localStorage.setItem(TOKEN_KEY, s.token);
  window.localStorage.setItem(USER_KEY, JSON.stringify({ username: s.username, role: s.role }));
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

type Query = Record<string, string | number | boolean | null | undefined>;

export function qs(params?: Query): string {
  if (!params) return "";
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") u.set(k, String(v));
  }
  const s = u.toString();
  return s ? `?${s}` : "";
}

export async function api<T = any>(
  path: string,
  opts: { method?: string; body?: unknown; params?: Query } = {},
): Promise<T> {
  const session = loadSession();
  const headers: Record<string, string> = {};
  if (session) headers.Authorization = `Bearer ${session.token}`;
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(`${PREFIX}${path}${qs(opts.params)}`, {
    method: opts.method ?? (opts.body !== undefined ? "POST" : "GET"),
    headers,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
  if (res.status === 401 && typeof window !== "undefined" && session) {
    saveSession(null);
    window.dispatchEvent(new Event("osint-auth-changed"));
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

export async function login(username: string, password: string): Promise<Session> {
  const r = await api<{ access_token: string; username: string; role: Session["role"] }>("/auth/login", {
    body: { username, password },
  });
  return { token: r.access_token, username: r.username, role: r.role };
}

/** Download raw artifact bytes (attachment; never rendered inline). */
export async function downloadArtifact(id: string): Promise<void> {
  const session = loadSession();
  const res = await fetch(`${PREFIX}/evidence/artifacts/${id}/content`, {
    headers: session ? { Authorization: `Bearer ${session.token}` } : {},
  });
  if (!res.ok) throw new ApiError(res.status, "artifact unavailable");
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `artifact-${id}.bin`;
  a.click();
  URL.revokeObjectURL(url);
}

// ---- shared shapes (only the fields the UI relies on)
export type Role = Session["role"];
export const ROLE_RANK: Record<Role, number> = { viewer: 0, analyst: 1, admin: 2 };

export interface GraphNode {
  id: string;
  label: string;
  entity_type: string;
  canonical_identifier: string;
  status: string;
  resolution_status: string;
  degree: number | null;
  verified: boolean;
  kind: string;
  network?: string | null;
  address?: string | null;
  hash?: string | null;
  url?: string | null;
}
export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  relationship_type: string;
  status: string;
  review_status: string;
  evidence_count: number;
  supporting: number;
  contradicting: number;
  distinct_sources: number;
  stale: boolean;
  derived: boolean;
  first_observed_at: string | null;
  last_observed_at: string | null;
}
export interface Subgraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
  truncated: boolean;
}
