import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import App from "./App";

vi.mock("./utils/api", () => ({
  api: {
    list: vi.fn().mockResolvedValue([]),
    create: vi.fn(),
    start: vi.fn(),
    decide: vi.fn(),
    outcome: vi.fn(),
    get: vi.fn(),
    trace: vi.fn(),
    githubStatus: vi.fn().mockResolvedValue({
      source: "github",
      repository: "demo-owner/demo-repo",
      ref: "main",
      last_commit_sha: "abc123",
      last_sync_at: "2026-09-15T10:00:00Z",
      imported_count: 20,
      rejected_count: 0,
      webhook_configured: false,
      allow_local_create: false,
    }),
    modelStatus: vi.fn().mockResolvedValue({
      available: true,
      kind: "xgboost",
      dataset_kind: "synthetic_heuristic_labels",
      trained_from_real_outcomes: false,
      rows: 2000,
      roc_auc: 0.64,
      accuracy: 0.61,
      note: "Synthetic simulator metrics, not real-world accuracy",
    }),
    githubSync: vi.fn(),
  },
  wsUrl: () => "ws://localhost/ws/test",
  apiBase: () => "/api",
}));

vi.mock("./hooks/usePipelineSocket", () => ({
  usePipelineSocket: () => true,
}));

describe("App", () => {
  it("shows GitHub source controls and hides the local create form", async () => {
    render(<App />);
    expect(await screen.findByText("GitHub source")).toBeInTheDocument();
    expect(screen.getAllByText("Sync GitHub cases").length).toBeGreaterThan(0);
    expect(screen.getByText("demo-owner/demo-repo")).toBeInTheDocument();
    expect(screen.queryByText("Create Dispute")).not.toBeInTheDocument();
    expect(screen.getAllByText("Filing simulated").length).toBeGreaterThan(0);
  });
});
