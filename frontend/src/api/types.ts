// Mirrors src/factoryai/api/schemas.py — the backend's published contract. Keep in sync by
// hand: there is no shared codegen step yet (a real gap, not a silent one; see ADR-0016).

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export type UserRole = "viewer" | "operator" | "ml_engineer" | "administrator";

export interface CurrentUser {
  user_id: string;
  email: string;
  role: UserRole;
}

export interface TokenResponse {
  access_token: string;
  refresh_token?: string | null;
  token_type: "bearer";
  expires_in: number;
}

export interface PredictionResponse {
  prediction_id: string;
  image_id: string;
  request_id?: string | null;
  anomaly_score: number;
  threshold: number;
  is_anomalous: boolean;
  confidence: number;
  inference_time_ms: number;
  model_version_id: string;
  dataset_version_id: string;
  heatmap_url?: string | null;
}

export interface PredictionHistoryItem {
  prediction_id: string;
  image_id: string;
  model_version_id: string;
  dataset_version_id: string;
  anomaly_score: number;
  threshold: number;
  is_anomalous: boolean;
  confidence: number;
  inference_time_ms: number;
  predicted_at: string;
  correlation_id?: string | null;
  image_url?: string | null;
  heatmap_url?: string | null;
}

export interface FeedbackRequest {
  prediction_id: string;
  verdict: "correct" | "incorrect";
  corrected_label?: "good" | "defect" | "unlabeled" | null;
  notes?: string;
  region?: [number, number, number, number] | null;
}

export interface ModelSummary {
  category: string;
  model_version_id?: string | null;
  registry_name?: string | null;
  registry_version?: number | null;
  threshold?: number | null;
  metrics?: Record<string, number | number[] | null> | null;
}

export interface ModelVersionItem {
  model_version_id: string;
  experiment_id: string;
  category: string;
  registry_name: string;
  registry_version: number;
  stage: "development" | "staging" | "production" | "archived";
  threshold: number;
  created_at: string;
}

export interface DriftSignal {
  name: string;
  statistic: number;
  threshold: number;
  method: string;
  breached: boolean;
}

export interface DriftReportItem {
  report_id: string;
  model_version_id: string;
  reference_dataset_version_id: string;
  window_start: string;
  window_end: string;
  sample_count: number;
  severity: "none" | "low" | "medium" | "high";
  should_trigger_retraining: boolean;
  signals: DriftSignal[];
  created_at: string;
}

export interface DatasetVersionItem {
  version_id: string;
  dataset_id: string;
  version_tag: string;
  dvc_hash: string;
  git_commit: string;
  image_count: number;
  note: string;
  created_at: string;
}

export interface TrainingRunItem {
  experiment_id: string;
  mlflow_run_id: string;
  dataset_version_id: string;
  model_family: string;
  backbone: string;
  status: "running" | "completed" | "failed";
  started_at: string;
  finished_at?: string | null;
  metrics?: Record<string, number | number[] | null> | null;
  failure_reason?: string | null;
}

export interface DeploymentItem {
  deployment_id: string;
  model_version_id: string;
  action: "promote" | "rollback" | "reject";
  environment: string;
  deployed_at: string;
  previous_model_version_id?: string | null;
  reason: string;
}

export interface DefectTrendPoint {
  day: string;
  total: number;
  defective: number;
  rate: number;
}

export interface SystemHealth {
  cpu_percent: number;
  memory_percent: number;
  disk_percent: number;
  jobs_by_status: Record<string, number>;
  model_cache_hit_ratio: number;
}

export interface JobResponse {
  job_id: string;
  job_type: string;
  status: "queued" | "running" | "succeeded" | "failed";
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  attempts: number;
  progress_completed: number;
  progress_total: number;
  result?: Record<string, unknown> | null;
  error?: string | null;
}
