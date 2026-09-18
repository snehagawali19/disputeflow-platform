import type { DisputeDetail, DisputeSession, GitHubStatus, GitHubSyncResult, ModelStatus } from "../types/api";

export function apiBase(): string {
  return "/api";
}

export function wsUrl(sessionId: string, after = 0): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/ws/${sessionId}?after=${after}`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBase()}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`${response.status}: ${detail}`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  list: () => request<DisputeSession[]>("/disputes"),
  create: (body: Record<string, unknown>) => request<{ session_id: string }>("/disputes", { method: "POST", body: JSON.stringify(body) }),
  start: (id: string) => request<{ status: string }>(`/disputes/${id}/start`, { method: "POST" }),
  decide: (id: string, decision: string, feedback = "") =>
    request(`/disputes/${id}/decide`, { method: "POST", body: JSON.stringify({ decision, feedback }) }),
  outcome: (id: string, outcome: string, feedback = "") =>
    request(`/disputes/${id}/outcome`, { method: "POST", body: JSON.stringify({ outcome, feedback }) }),
  get: (id: string) => request<DisputeDetail>(`/disputes/${id}`),
  trace: (id: string) => request(`/disputes/${id}/trace`),
  githubStatus: () => request<GitHubStatus>("/github/status"),
  modelStatus: () => request<ModelStatus>("/model/status"),
  githubSync: () => request<GitHubSyncResult>("/github/sync", { method: "POST" }),
  githubFetchOneAndStart: () => request<{ session_id: string; file_path: string; status: string }>("/github/fetch-one-and-start", { method: "POST" }),
};
