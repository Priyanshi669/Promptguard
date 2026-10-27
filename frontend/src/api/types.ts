/**
 * Typed mirrors of the PromptGuard API response schemas
 * (src/promptguard/interfaces/api/schemas.py). The API cannot invent
 * fields the domain does not carry; neither does this client.
 */

export interface ApiErrorEnvelope {
  error: {
    code: string;
    message: string;
    request_id?: string | null;
    details?: Record<string, unknown> | null;
  };
}

export type RunJobStatus =
  | "queued"
  | "running"
  | "cancelled"
  | "succeeded"
  | "quality_gate_failed"
  | "inconclusive"
  | "error";

export interface HealthResponse {
  status: string;
}

export interface ReadinessResponse {
  ready: boolean;
  checks: Record<string, boolean>;
  auth_mode: string;
  issues: string[];
}

export interface ProviderPoolEntry {
  name: string;
  model: string;
}

export interface ConfigResponse {
  candidate_model: string;
  candidate_base_url: string;
  judge_model: string;
  judge_base_url: string;
  request_timeout_seconds: number;
  min_request_interval_seconds: number;
  evaluation_timeout_seconds: number;
  bootstrap: {
    seed: number;
    resamples: number;
    confidence_level: number;
  };
  provider_pool: ProviderPoolEntry[];
  prompt_versions: string[];
  datasets: Record<string, number>;
  credentials_in_identities: boolean;
}

export interface DatasetSummaryResponse {
  prompt_version: string;
  case_count: number;
  case_count_by_category: Record<string, number>;
}

export interface RunListItem {
  run_id: string;
  timestamp: string;
  model: string;
  dataset: string;
  prompt_version: string;
  case_count: number;
  category_accuracy: number;
  average_summary_score: number;
  average_latency_ms: number;
  regression_status: string | null;
  baseline_run_id: string | null;
  eval_config_hash: string | null;
}

export interface RunListResponse {
  items: RunListItem[];
  total: number;
  offset: number;
  limit: number;
  skipped_runs: string[];
}

export interface BaselineListItem extends RunListItem {
  critical_count: number;
  has_thresholds: boolean;
}

export interface BaselineListResponse {
  items: BaselineListItem[];
  total: number;
  offset: number;
  limit: number;
  note: string;
}

/** Per-case evaluation result (EvaluationResult in domain models). */
export interface EvaluationResult {
  test_case_id: string;
  expected_category: string;
  actual_category: string;
  expected_summary: string;
  actual_summary: string;
  category_match: boolean;
  summary_score: number;
  latency_ms: number;
  input_tokens: number;
  output_tokens: number;
}

export interface ClassificationMetrics {
  accuracy: number;
  categories: Record<
    string,
    { support: number; tp: number; fp: number; precision: number; recall: number; f1: number }
  >;
  macro_f1: number;
  weighted_f1: number;
  confusion_matrix: Record<string, Record<string, number>>;
}

export interface ConfidenceIntervals {
  category_accuracy: { lower: number; upper: number };
  average_summary_score: { lower: number; upper: number };
  resamples: number;
  seed: number;
  confidence_level: number;
}

/** Reused verbatim from the domain; unknown fields are passed through. */
export interface EvaluationReport {
  run_id: string;
  timestamp: string;
  prompt_version: string;
  model: string;
  dataset: string;
  case_count: number;
  category_accuracy: number;
  average_summary_score: number;
  average_latency_ms: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_cost_usd: number | null;
  schema_version: number;
  results: EvaluationResult[];
  classification: ClassificationMetrics | null;
  confidence_intervals: ConfidenceIntervals | null;
  paired_analysis: Record<string, unknown> | null;
  eval_config_hash: string | null;
  evaluator: Record<string, unknown> | null;
  thresholds: Record<string, unknown> | null;
  baseline_run_id: string | null;
  comparison: Record<string, unknown> | null;
  regression: {
    status: string;
    violations: Array<Record<string, unknown>>;
    case_regressions: Array<{ test_case_id: string; status: string }>;
    critical_case_failure: boolean;
    incompatibility_reasons: string[];
  } | null;
  drift: Record<string, unknown> | null;
}

export interface RunDetailResponse {
  job: RunJobStatusResponse | null;
  report: EvaluationReport;
}

export interface RunCreateRequest {
  prompt_version?: string;
  limit?: number | null;
  baseline_run_id?: string | null;
  min_category_accuracy?: number | null;
  min_summary_score?: number | null;
  max_latency_ms?: number | null;
  max_category_accuracy_drop?: number | null;
  max_summary_score_drop?: number | null;
  max_category_accuracy_drop_pct?: number | null;
  max_summary_score_drop_pct?: number | null;
}

export interface RunCreatedResponse {
  job_id: string;
  status: RunJobStatus;
  created_at: string;
  duplicate: boolean;
}

export interface RunJobStatusResponse {
  job_id: string;
  status: RunJobStatus;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  error_code: string | null;
  error_message: string | null;
  report_run_id: string | null;
  signature_hash: string;
}

export interface RunCancelResponse {
  job_id: string;
  status: RunJobStatus;
  cancelled: boolean;
}

export type DraftState =
  | "missing"
  | "prepared_final_draft";

export type ValidationState =
  | "not_run"
  | "error"
  | "draft_with_defects"
  | "clean_draft_awaiting_reviewer_fields";

export type CalibrationState =
  | "not_executed"
  | "executed_report_available";

export interface CandidateProvenance {
  candidate_model: string;
  candidate_run_id: string;
  candidate_generated_at: string;
  candidate_status: string;
  prompt_version: string;
}

export interface DraftRow {
  id: string;
  annotation_pass: string;
  is_double_annotated: boolean;
  source_split: string;
  category: string;
  source_category: string;
  inclusion_status: string;
  source_input: string;
  expected_summary: string;
  actual_summary: string;
  actual_summary_is_ai_candidate: boolean;
  provisional_reference: boolean;
  reference_is_human_validated: boolean;
  human_score: string;
  confidence: string;
  notes: string;
  annotator_id: string;
  annotated_at: string;
  candidate: CandidateProvenance | null;
}

export interface DraftFinding {
  row_id: string;
  field: string;
  finding_type: string;
  message: string;
  hint: string | null;
}

export interface StrictValidationSummary {
  validator: string;
  status: string;
  total_rows: number | null;
  error_count: number;
  pending_reviewer_field_count: number;
  defect_count: number;
  defects: DraftFinding[];
  raw_errors: string[];
  warning_count: number;
}

export interface DraftOverview {
  draft_state: DraftState;
  draft_version: string;
  total_rows: number;
  unique_cases: number;
  primary_rows: number;
  secondary_rows: number;
  scored_rows: number;
  scored_unique_cases: number;
  pending_reviewer_rows: number;
  double_annotation_cases: number;
  candidate_run_ids: string[];
  candidate_models: string[];
  validation_state: ValidationState;
  calibration_state: CalibrationState;
  calibration_report_path: string | null;
  strict_validation: StrictValidationSummary | null;
  provenance_notes: string[];
}

export interface DraftOverviewResponse {
  overview: DraftOverview;
  note: string;
}

export interface CalibrationRowsResponse {
  items: DraftRow[];
  total: number;
  offset: number;
  limit: number;
}
