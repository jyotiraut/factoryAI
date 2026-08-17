import { apiRequest, apiUpload } from "./client";
import type {
  CurrentUser,
  DatasetVersionItem,
  DefectTrendPoint,
  DeploymentItem,
  DriftReportItem,
  FeedbackRequest,
  ModelSummary,
  ModelVersionItem,
  Page,
  PredictionHistoryItem,
  PredictionResponse,
  SystemHealth,
  TokenResponse,
  TrainingRunItem,
} from "./types";

export function login(email: string, password: string): Promise<TokenResponse> {
  return apiRequest<TokenResponse>("/auth/login", {
    method: "POST",
    body: { email, password },
  });
}

export function logout(refreshToken: string): Promise<void> {
  return apiRequest<void>("/auth/logout", { method: "POST", body: { refresh_token: refreshToken } });
}

export function getCurrentUser(): Promise<CurrentUser> {
  return apiRequest<CurrentUser>("/auth/me");
}

export function listModels(): Promise<ModelSummary[]> {
  return apiRequest<ModelSummary[]>("/models");
}

export function listModelVersions(category: string): Promise<ModelVersionItem[]> {
  return apiRequest<ModelVersionItem[]>("/models/versions", { query: { category } });
}

export function listDeployments(
  category: string,
  environment = "production",
  limit = 50,
): Promise<DeploymentItem[]> {
  return apiRequest<DeploymentItem[]>("/models/deployments", {
    query: { category, environment, limit },
  });
}

export function listPredictions(
  limit: number,
  offset: number,
  modelVersionId?: string,
): Promise<Page<PredictionHistoryItem>> {
  return apiRequest<Page<PredictionHistoryItem>>("/predictions", {
    query: { limit, offset, model_version_id: modelVersionId },
  });
}

export function listFeedbackQueue(limit: number, offset: number): Promise<Page<PredictionHistoryItem>> {
  return apiRequest<Page<PredictionHistoryItem>>("/predictions/feedback-queue", {
    query: { limit, offset },
  });
}

export function submitFeedback(payload: FeedbackRequest): Promise<{ feedback_id: string }> {
  return apiRequest<{ feedback_id: string }>("/feedback", { method: "POST", body: payload });
}

export function listDriftReports(
  limit: number,
  offset: number,
  modelVersionId?: string,
): Promise<Page<DriftReportItem>> {
  return apiRequest<Page<DriftReportItem>>("/drift/reports", {
    query: { limit, offset, model_version_id: modelVersionId },
  });
}

export function listDatasetVersions(limit: number, offset: number): Promise<Page<DatasetVersionItem>> {
  return apiRequest<Page<DatasetVersionItem>>("/datasets/versions", { query: { limit, offset } });
}

export function listTrainingRuns(limit: number, offset: number): Promise<Page<TrainingRunItem>> {
  return apiRequest<Page<TrainingRunItem>>("/training/runs", { query: { limit, offset } });
}

export function getDefectTrend(category: string, days = 30): Promise<DefectTrendPoint[]> {
  return apiRequest<DefectTrendPoint[]>("/analytics/defect-trend", { query: { category, days } });
}

export function getSystemHealth(): Promise<SystemHealth> {
  return apiRequest<SystemHealth>("/system/health");
}

export function predictImage(category: string, image: File): Promise<PredictionResponse> {
  const form = new FormData();
  form.set("category", category);
  form.set("image", image);
  return apiUpload<PredictionResponse>("/predict", form);
}
