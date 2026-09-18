export interface DisputeSession {
  session_id: string;
  case_id: string;
  status: string;
  amount: number;
  currency?: string;
  reason_code: string;
  created_at: string;
  pipeline_status?: string;
  source_type?: string;
  source_repository?: string;
  needs_human_review?: boolean;
}

export interface PipelineEvent {
  event: string;
  stage?: string;
  next_stage?: string;
  case_status?: string;
  needs_human_approval?: boolean;
  error?: string;
  sequence?: number;
  events?: PipelineEvent[];
  decision?: string;
}

export interface GitHubStatus {
  source: string;
  repository: string;
  ref: string;
  last_commit_sha: string;
  last_sync_at: string | null;
  imported_count: number;
  rejected_count: number;
  webhook_configured: boolean;
  allow_local_create: boolean;
}

export interface GitHubSyncResult {
  imported: number;
  updated: number;
  unchanged: number;
  rejected: number;
  commit_sha: string;
}

export interface DisputeDetail {
  session_id: string;
  status: string;
  version: number;
  case: Record<string, unknown>;
  source?: Record<string, unknown>;
}

export interface ModelStatus {
  available: boolean;
  kind: string;
  dataset_kind: string;
  trained_from_real_outcomes: boolean;
  rows?: number;
  roc_auc?: number;
  accuracy?: number;
  note: string;
}
