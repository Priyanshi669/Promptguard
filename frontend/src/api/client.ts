/**
 * Typed HTTP client for the PromptGuard API (/api/v1).
 *
 * Every non-2xx response is expected to use the canonical error
 * envelope ("error": {code, message, request_id?, details?}); failures
 * that do not (proxies, network) surface as transport errors with the
 * raw status so the UI never confuses them with domain errors.
 */

import type {
  ApiErrorEnvelope,
  BaselineListResponse,
  CalibrationRowsResponse,
  ConfigResponse,
  DatasetSummaryResponse,
  DraftOverviewResponse,
  HealthResponse,
  ReadinessResponse,
  RunCancelResponse,
  RunCreateRequest,
  RunCreatedResponse,
  RunDetailResponse,
  RunJobStatusResponse,
  RunListResponse,
} from "./types";

const API_PREFIX = "/api/v1";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string | null;
  readonly details: Record<string, unknown> | null;

  constructor(
    status: number,
    code: string,
    message: string,
    requestId: string | null = null,
    details: Record<string, unknown> | null = null,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
    this.details = details;
  }
}

export class TransportError extends Error {
  readonly status: number;

  constructor(status: number, raw: string) {
    super(
      `Unexpected response (${status}); body was not the documented error envelope.`,
    );
    this.name = "TransportError";
    this.status = status;
    this.raw = raw;
  }

  readonly raw: string;
}

export type ApiTokenProvider = () => string | null;

let tokenProvider: ApiTokenProvider = () => null;

/** Configure the bearer token used for mutating requests (optional). */
export function setApiTokenProvider(provider: ApiTokenProvider): void {
  tokenProvider = provider;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  if (init?.body != null) {
    headers.set("Content-Type", "application/json");
  }
  const token = tokenProvider();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  const response = await fetch(`${API_PREFIX}${path}`, { ...init, headers });
  if (response.ok) {
    return (await response.json()) as T;
  }
  const raw = await response.text();
  let parsed: unknown = null;
  try {
    parsed = JSON.parse(raw);
  } catch {
    throw new TransportError(response.status, raw || response.statusText);
  }
  const envelope = parsed as ApiErrorEnvelope;
  if (envelope?.error?.code) {
    throw new ApiError(
      response.status,
      envelope.error.code,
      envelope.error.message,
      envelope.error.request_id ?? null,
      envelope.error.details ?? null,
    );
  }
  throw new TransportError(response.status, raw || response.statusText);
}

export function health(): Promise<HealthResponse> {
  return request<HealthResponse>("/health");
}

export function readiness(): Promise<ReadinessResponse> {
  return request<ReadinessResponse>("/readiness");
}

export function config(): Promise<ConfigResponse> {
  return request<ConfigResponse>("/config");
}

export function datasetSummary(
  promptVersion: string,
): Promise<DatasetSummaryResponse> {
  return request<DatasetSummaryResponse>(
    `/datasets/${encodeURIComponent(promptVersion)}`,
  );
}

export function listRuns(
  offset = 0,
  limit = 20,
): Promise<RunListResponse> {
  const query = new URLSearchParams({
    offset: String(offset),
    limit: String(limit),
  });
  return request<RunListResponse>(`/runs?${query.toString()}`);
}

export function getRun(runId: string): Promise<RunDetailResponse> {
  return request<RunDetailResponse>(`/runs/${encodeURIComponent(runId)}`);
}

/** Direct file-download URLs for report exports. */
export function runExportUrl(runId: string, format: "json" | "text"): string {
  return `${API_PREFIX}/runs/${encodeURIComponent(runId)}/export?format=${format}`;
}

export function createRun(
  body: RunCreateRequest,
): Promise<RunCreatedResponse> {
  return request<RunCreatedResponse>("/runs", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function jobStatus(jobId: string): Promise<RunJobStatusResponse> {
  return request<RunJobStatusResponse>(
    `/run-jobs/${encodeURIComponent(jobId)}`,
  );
}

export function cancelJob(jobId: string): Promise<RunCancelResponse> {
  return request<RunCancelResponse>(
    `/run-jobs/${encodeURIComponent(jobId)}`,
    { method: "DELETE" },
  );
}

export function listBaselines(
  offset = 0,
  limit = 50,
): Promise<BaselineListResponse> {
  const query = new URLSearchParams({
    offset: String(offset),
    limit: String(limit),
  });
  return request<BaselineListResponse>(`/baselines?${query.toString()}`);
}

export function calibrationOverview(): Promise<DraftOverviewResponse> {
  return request<DraftOverviewResponse>("/calibration/draft");
}

export function calibrationRows(
  offset = 0,
  limit = 25,
): Promise<CalibrationRowsResponse> {
  const query = new URLSearchParams({
    offset: String(offset),
    limit: String(limit),
  });
  return request<CalibrationRowsResponse>(`/calibration/draft/rows?${query}`);
}

export const TERMINAL_JOB_STATUSES = new Set([
  "cancelled",
  "succeeded",
  "quality_gate_failed",
  "inconclusive",
  "error",
]);
