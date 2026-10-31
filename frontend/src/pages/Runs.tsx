import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  ApiError,
  cancelJob,
  createRun,
  getRun,
  jobStatus,
  listBaselines,
  listRuns,
  runExportUrl,
  TERMINAL_JOB_STATUSES,
} from "../api/client";
import type {
  BaselineListResponse,
  EvaluationReport,
  RunCreateRequest,
  RunDetailResponse,
  RunJobStatusResponse,
  RunListResponse,
} from "../api/types";
import {
  EmptyState,
  ErrorBanner,
  GateDot,
  Loading,
  Metric,
  Panel,
  StatusBadge,
  formatTimestamp,
  pct,
} from "../components/ui";

const POLL_INTERVAL_MS = 2000;

interface RunDraftForm {
  promptVersion: string;
  limit: string;
  baseline: string;
  minCategoryAccuracy: string;
  minSummaryScore: string;
  maxLatencyMs: string;
  maxCategoryAccuracyDrop: string;
  maxSummaryScoreDrop: string;
  maxCategoryDrift: string;
}

const EMPTY_FORM: RunDraftForm = {
  promptVersion: "v1",
  limit: "",
  baseline: "latest",
  minCategoryAccuracy: "",
  minSummaryScore: "",
  maxLatencyMs: "",
  maxCategoryAccuracyDrop: "",
  maxSummaryScoreDrop: "",
  maxCategoryDrift: "",
};

type NumberFields = Pick<
  RunCreateRequest,
  | "limit"
  | "min_category_accuracy"
  | "min_summary_score"
  | "max_latency_ms"
  | "max_category_accuracy_drop"
  | "max_summary_score_drop"
  | "max_category_drift"
>;

function toNumber(value: string): number | undefined {
  const trimmed = value.trim();
  if (!trimmed) {
    return undefined;
  }
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function Field({
  id,
  label,
  value,
  placeholder,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  placeholder: string;
  onChange: (value: string) => void;
}) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}

export function RunsPage() {
  const [runs, setRuns] = useState<RunListResponse | null>(null);
  const [baselines, setBaselines] = useState<BaselineListResponse | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [gateFilter, setGateFilter] = useState<"all" | "passed" | "failed" | "inconclusive">("all");
  const [form, setForm] = useState<RunDraftForm>(EMPTY_FORM);
  const [confirming, setConfirming] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [job, setJob] = useState<RunJobStatusResponse | null>(null);
  const [duplicateNote, setDuplicateNote] = useState<string | null>(null);
  const pollTimer = useRef<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [runsRes, baselinesRes] = await Promise.all([
        listRuns(0, 50),
        listBaselines(),
      ]);
      setRuns(runsRes);
      setBaselines(baselinesRes);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    return () => {
      if (pollTimer.current !== null) {
        window.clearInterval(pollTimer.current);
      }
    };
  }, []);

  const poll = useCallback(
    (jobId: string) => {
      if (pollTimer.current !== null) {
        window.clearInterval(pollTimer.current);
      }
      pollTimer.current = window.setInterval(async () => {
        try {
          const status = await jobStatus(jobId);
          setJob(status);
          if (TERMINAL_JOB_STATUSES.has(status.status)) {
            if (pollTimer.current !== null) {
              window.clearInterval(pollTimer.current);
              pollTimer.current = null;
            }
            void load();
          }
        } catch {
          // Transient polling failures never kill the watch loop.
        }
      }, POLL_INTERVAL_MS);
    },
    [load],
  );

  const visibleRuns = useMemo(() => {
    if (!runs) {
      return [];
    }
    const query = search.trim().toLowerCase();
    return runs.items.filter((run) => {
      const gate = run.regression_status ?? "INCONCLUSIVE";
      if (
        gateFilter !== "all" &&
        gate.toUpperCase() !== gateFilter.toUpperCase()
      ) {
        return false;
      }
      if (!query) {
        return true;
      }
      const haystack = `${run.run_id} ${run.model} ${run.dataset}`;
      return haystack.toLowerCase().includes(query);
    });
  }, [runs, gateFilter, search]);

  const pendingNumbers: NumberFields = {
    limit: toNumber(form.limit),
    min_category_accuracy: toNumber(form.minCategoryAccuracy),
    min_summary_score: toNumber(form.minSummaryScore),
    max_latency_ms: toNumber(form.maxLatencyMs),
    max_category_accuracy_drop: toNumber(form.maxCategoryAccuracyDrop),
    max_summary_score_drop: toNumber(form.maxSummaryScoreDrop),
    max_category_drift: toNumber(form.maxCategoryDrift),
  };

  const payload: RunCreateRequest = {
    prompt_version: form.promptVersion,
    ...pendingNumbers,
    baseline_run_id: form.baseline.trim() ? form.baseline.trim() : null,
  };

  async function submit(): Promise<void> {
    setSubmitting(true);
    setDuplicateNote(null);
    setError(null);
    try {
      const created = await createRun(payload);
      setJob({
        job_id: created.job_id,
        status: created.status,
        created_at: created.created_at,
        started_at: null,
        finished_at: null,
        error_code: null,
        error_message: null,
        report_run_id: null,
        signature_hash: "",
      });
      if (created.duplicate) {
        setDuplicateNote(
          "An identical run request was already queued or running; watching that job instead of queueing a duplicate.",
        );
      }
      setConfirming(false);
      poll(created.job_id);
    } catch (err) {
      setError(err);
      setConfirming(false);
    } finally {
      setSubmitting(false);
    }
  }

  async function cancel(): Promise<void> {
    if (!job) {
      return;
    }
    setError(null);
    try {
      const cancelled = await cancelJob(job.job_id);
      setJob((current) =>
        current ? { ...current, status: cancelled.status } : current,
      );
      if (pollTimer.current !== null) {
        window.clearInterval(pollTimer.current);
        pollTimer.current = null;
      }
      void load();
    } catch (err) {
      setError(err);
    }
  }

  return (
    <div className="flex flex-col w-full">
      {/* Command strip */}
      <div className="w-full bg-surface-container-lowest px-gutter py-space-sm flex flex-wrap items-center justify-between gap-space-md">
        <div className="flex items-center gap-1.5 font-code-sm text-code-sm text-outline">
          <span className="text-on-surface font-medium">Evaluation runs</span>
          {runs && (
            <span className="font-code-sm text-code-sm px-1.5 py-0.5 rounded bg-surface-container text-on-surface-variant">
              {runs.total}
            </span>
          )}
        </div>
      </div>

      <div className="w-full p-gutter flex flex-col gap-space-md">
        <ErrorBanner error={error} />
        {loading && <Loading />}

        {runs && !loading && (
          <div className="w-full bg-surface-container-lowest rounded overflow-hidden flex flex-col">
            {/* Toolbar: filters + search */}
            <div className="px-space-md py-space-sm bg-surface-container-low flex flex-wrap items-center justify-between gap-space-sm">
              <div className="flex items-center gap-space-sm">
                <span className="font-headline-sm text-headline-sm text-on-surface">
                  Run history
                </span>
                <select
                  className="h-7 px-space-xs bg-surface-container rounded font-code-sm text-code-sm text-on-surface focus:outline-none cursor-pointer"
                  value={gateFilter}
                  onChange={(e) =>
                    setGateFilter(e.target.value as typeof gateFilter)
                  }
                >
                  <option value="all">Gate: All</option>
                  <option value="passed">Passed</option>
                  <option value="failed">Failed</option>
                  <option value="inconclusive">Inconclusive</option>
                </select>
              </div>
              <div className="relative w-full max-w-[220px]">
                <span className="material-symbols-outlined absolute left-2 top-1.5 text-[14px] text-outline">
                  search
                </span>
                <input
                  className="w-full h-7 pl-6 pr-2 bg-surface-container rounded font-code-sm text-code-sm text-on-surface placeholder:text-outline focus:outline-none"
                  placeholder="Filter run id, model, dataset…"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              </div>
            </div>
            <div className="w-full overflow-x-auto">
              {visibleRuns.length === 0 ? (
                <div className="p-space-md">
                  {runs.items.length === 0 ? (
                    <EmptyState>
                      No persisted runs yet.{" "}
                      {runs.skipped_runs.length > 0
                        ? `${runs.skipped_runs.length} run files were skipped as unreadable — see the API for diagnostics.`
                        : ""}
                    </EmptyState>
                  ) : (
                    <EmptyState>No runs match the current filters.</EmptyState>
                  )}
                </div>
              ) : (
                <table className="w-full text-left border-collapse">
                  <thead>
                    <tr className="bg-surface-container-low font-label-sm text-label-sm text-outline uppercase h-7">
                      <th className="px-space-md font-medium">Run ID</th>
                      <th className="px-space-md font-medium">Model</th>
                      <th className="px-space-md font-medium">Dataset</th>
                      <th className="px-space-md font-medium">Timestamp</th>
                      <th className="px-space-md font-medium text-right">
                        Accuracy
                      </th>
                      <th className="px-space-md font-medium text-right">
                        Score
                      </th>
                      <th className="px-space-md font-medium text-right">
                        Latency
                      </th>
                      <th className="px-space-md font-medium text-center">
                        Gate
                      </th>
                      <th className="px-space-md font-medium text-right">
                        Action
                      </th>
                    </tr>
                  </thead>
                  <tbody className="font-code-sm text-code-sm divide-y divide-surface-container">
                    {visibleRuns.map((run) => (
                      <tr
                        key={run.run_id}
                        className="hover:bg-surface-container transition-colors"
                      >
                        <td className="px-space-md py-space-sm whitespace-nowrap">
                          <div className="flex items-center gap-1.5">
                            <GateDot status={run.regression_status} />
                            <Link
                              to={`/runs/${run.run_id}`}
                              className="font-code-md text-code-md text-primary font-medium"
                            >
                              {run.run_id.slice(0, 8)}
                            </Link>
                          </div>
                        </td>
                        <td className="px-space-md py-space-sm max-w-[180px] truncate text-on-surface">
                          {run.model}
                        </td>
                        <td className="px-space-md py-space-sm text-on-surface-variant font-mono">
                          {run.dataset}
                        </td>
                        <td className="px-space-md py-space-sm text-outline whitespace-nowrap">
                          {formatTimestamp(run.timestamp)}
                        </td>
                        <td className="px-space-md py-space-sm text-right font-medium text-on-surface">
                          {pct(run.category_accuracy)}
                        </td>
                        <td className="px-space-md py-space-sm text-right text-on-surface">
                          {run.average_summary_score.toFixed(2)}
                        </td>
                        <td className="px-space-md py-space-sm text-right text-outline font-mono">
                          {Math.round(run.average_latency_ms)} ms
                        </td>
                        <td className="px-space-md py-space-sm text-center">
                          <StatusBadge status={run.regression_status} />
                        </td>
                        <td className="px-space-md py-space-sm text-right">
                          <Link
                            to={`/runs/${run.run_id}`}
                            className="px-2 py-0.5 rounded bg-surface-container hover:bg-surface-container-high text-primary font-code-sm text-code-sm"
                          >
                            Inspect
                          </Link>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
            <div className="px-space-md py-space-xs bg-surface-container-low flex items-center justify-between text-outline font-code-sm text-code-sm">
              <span>
                Showing {visibleRuns.length} of{" "}
                {`${runs.items.length} (max 50), ${runs.total} total`}
              </span>
              <div className="flex items-center gap-space-xs">
                <a
                  href={runExportUrl(runs.items[0]?.run_id ?? "", "json")}
                  download
                  className="px-2 py-0.5 rounded bg-surface-container text-outline hover:text-on-surface"
                >
                  Export latest JSON
                </a>
                <a
                  href={runExportUrl(runs.items[0]?.run_id ?? "", "text")}
                  download
                  className="px-2 py-0.5 rounded bg-surface-container text-outline hover:text-on-surface"
                >
                  text
                </a>
              </div>
            </div>
          </div>
        )}

        {/* Queue a run */}
        {runs && (
          <Panel
            title="Queue a run"
            actions={
              runs.total === 0 && baselines && baselines.total === 0 ? null : (
                <span className="font-code-sm text-code-sm text-outline normal-case tracking-normal">
                  {runs.total === 0 && baselines
                    ? "No baselines exist: no comparison"
                    : null}
                </span>
              )
            }
          >
            {!confirming && !job && (
              <form
                className="flex flex-col gap-space-md"
                onSubmit={(event) => {
                  event.preventDefault();
                  setConfirming(true);
                }}
              >
                <div className="field-row">
                  <Field
                    id="prompt-version"
                    label="Prompt version"
                    value={form.promptVersion}
                    placeholder="v1"
                    onChange={(value) =>
                      setForm({ ...form, promptVersion: value })
                    }
                  />
                  <Field
                    id="limit"
                    label="Limit (first N cases)"
                    value={form.limit}
                    placeholder="all"
                    onChange={(value) => setForm({ ...form, limit: value })}
                  />
                  <Field
                    id="baseline"
                    label="Baseline run id (or “latest”)"
                    value={form.baseline}
                    placeholder="latest"
                    onChange={(value) => setForm({ ...form, baseline: value })}
                  />
                </div>
                <div className="field-row">
                  <Field
                    id="min-acc"
                    label="Min category accuracy"
                    value={form.minCategoryAccuracy}
                    placeholder="unset"
                    onChange={(value) =>
                      setForm({ ...form, minCategoryAccuracy: value })
                    }
                  />
                  <Field
                    id="min-score"
                    label="Min summary score"
                    value={form.minSummaryScore}
                    placeholder="unset"
                    onChange={(value) =>
                      setForm({ ...form, minSummaryScore: value })
                    }
                  />
                  <Field
                    id="max-latency"
                    label="Max latency (ms)"
                    value={form.maxLatencyMs}
                    placeholder="unset"
                    onChange={(value) =>
                      setForm({ ...form, maxLatencyMs: value })
                    }
                  />
                </div>
                <div className="field-row">
                  <Field
                    id="acc-drop"
                    label="Max accuracy drop"
                    value={form.maxCategoryAccuracyDrop}
                    placeholder="unset"
                    onChange={(value) =>
                      setForm({ ...form, maxCategoryAccuracyDrop: value })
                    }
                  />
                  <Field
                    id="score-drop"
                    label="Max summary score drop"
                    value={form.maxSummaryScoreDrop}
                    placeholder="unset"
                    onChange={(value) =>
                      setForm({ ...form, maxSummaryScoreDrop: value })
                    }
                  />
                  <Field
                    id="max-drift"
                    label="Max category drift (0–1)"
                    value={form.maxCategoryDrift}
                    placeholder="unset: no drift alerts"
                    onChange={(value) =>
                      setForm({ ...form, maxCategoryDrift: value })
                    }
                  />
                </div>
                <p className="notes-list">
                  Blank thresholds stay unset; an empty dataset of gates
                  produces an INCONCLUSIVE gate by design. A max drift value
                  enables Slack drift alerts when Slack is configured.
                </p>
                <button type="submit" className="primary self-start">
                  Review and submit
                </button>
              </form>
            )}

            {confirming && (
              <div className="flex flex-col gap-space-sm">
                <h2 className="font-headline-sm text-headline-sm text-on-surface">
                  Confirm run request
                </h2>
                <ul className="notes-list">
                  <li>Prompt version: {payload.prompt_version}</li>
                  <li>Limit: {payload.limit ?? "full dataset"}</li>
                  <li>
                    Baseline:{" "}
                    {payload.baseline_run_id ?? "none (no comparison)"}
                  </li>
                  <li>
                    Thresholds:{" "}
                    {Object.entries(payload)
                      .filter(
                        ([key, value]) =>
                          key !== "prompt_version" &&
                          key !== "limit" &&
                          key !== "baseline_run_id" &&
                          value != null,
                      )
                      .map(([key, value]) => `${key}=${String(value)}`)
                      .join(", ") || "none — gate will be INCONCLUSIVE"}
                  </li>
                </ul>
                <div className="flex gap-space-sm">
                  <button
                    type="button"
                    className="primary"
                    onClick={() => void submit()}
                    disabled={submitting}
                  >
                    {submitting ? "Submitting…" : "Confirm and queue"}
                  </button>
                  <button type="button" onClick={() => setConfirming(false)}>
                    Back to form
                  </button>
                </div>
              </div>
            )}

            {job && (
              <div className="flex flex-col gap-space-sm">
                <h2 className="font-headline-sm text-headline-sm text-on-surface">
                  Run status
                </h2>
                {duplicateNote && <p className="notes-list">{duplicateNote}</p>}
                <div className="grid">
                  <Metric label="Job" value={job.job_id} mono />
                  <Metric label="Status" value={job.status} />
                  <Metric
                    label="Report"
                    value={job.report_run_id ?? "—"}
                    mono
                  />
                </div>
                {job.error_message && (
                  <div className="error-banner">
                    <span className="font-code-sm text-code-sm mr-space-sm">
                      {job.error_code}
                    </span>
                    {job.error_message}
                  </div>
                )}
                {!TERMINAL_JOB_STATUSES.has(job.status) && (
                  <button
                    type="button"
                    onClick={() => void cancel()}
                    className="self-start"
                  >
                    Cancel run
                  </button>
                )}
                {TERMINAL_JOB_STATUSES.has(job.status) &&
                  job.status === "quality_gate_failed" && (
                  <p className="notes-list">
                    The run completed but failed the quality gate; the
                    persisted report still carries the full evidence.
                  </p>
                )}
                {job.report_run_id && (
                  <Link
                    to={`/runs/${job.report_run_id}`}
                    className="text-primary hover:text-primary-fixed font-code-sm text-code-sm"
                  >
                    Open the report →
                  </Link>
                )}
              </div>
            )}
          </Panel>
        )}

        {error !== null && !runs && <Loading />}
        {runs === null && !loading && error === null && (
          <EmptyState>Run data unavailable.</EmptyState>
        )}
      </div>
    </div>
  );
}

export function RunDetailPage() {
  const { runId } = useParams<{ runId: string }>();
  const [detail, setDetail] = useState<RunDetailResponse | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    if (!runId) {
      setError(new ApiError(400, "bad_request", "Run id missing."));
      setLoading(false);
      return;
    }
    getRun(runId)
      .then((data) => {
        if (!cancelled) {
          setDetail(data);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [runId]);

  if (loading) {
    return (
      <div>
        <h1>Run detail</h1>
        <Loading />
      </div>
    );
  }
  if (error !== null) {
    return (
      <div>
        <h1>Run detail</h1>
        <ErrorBanner error={error} />
      </div>
    );
  }
  if (!detail) {
    return (
      <div>
        <h1>Run detail</h1>
        <EmptyState>No report loaded.</EmptyState>
      </div>
    );
  }

  const r = detail.report;
  const classification = r.classification;
  const comparison = r.comparison as {
    baseline_run_id?: string;
    category_accuracy_delta?: number;
    summary_score_delta?: number;
    latency_delta_ms?: number;
  } | null;
  const drift = r.drift as {
    sufficient_data?: boolean;
    total_variation_distance?: number | null;
    baseline_distribution?: Record<string, number>;
    current_distribution?: Record<string, number>;
  } | null;
  const gateStatus = r.regression?.status ?? "INCONCLUSIVE";

  return (
    <div className="flex flex-col w-full">
      {/* Top evaluation header tier */}
      <div className="w-full bg-surface-container-low px-gutter py-space-md flex flex-col gap-space-sm">
        <div className="flex flex-wrap items-center justify-between gap-space-md">
          <div className="flex flex-col gap-0.5">
            <div className="flex items-center gap-space-xs font-code-sm text-code-sm text-outline">
              <Link to="/runs" className="hover:text-on-surface">
                Evaluation Runs
              </Link>
              <span>/</span>
              <span className="text-on-surface-variant font-medium font-mono">
                {r.run_id}
              </span>
              <span>/</span>
              <span className="text-primary font-medium">Report</span>
            </div>
            <div className="flex items-center gap-space-sm pt-0.5">
              <h1 className="font-headline-md text-headline-md text-on-surface tracking-tight">
                {r.model} — {r.dataset}
              </h1>
              <StatusBadge status={gateStatus} />
            </div>
          </div>
          <div className="flex items-center gap-space-xs">
            <a
              className="flex items-center gap-1 px-2.5 py-1 rounded bg-primary text-on-primary font-code-sm text-code-sm font-medium hover:bg-primary-fixed transition-colors"
              href={runExportUrl(r.run_id, "json")}
              download
            >
              <span className="material-symbols-outlined text-[14px]">
                download
              </span>
              <span>Download JSON Report</span>
            </a>
            <a
              className="flex items-center gap-1 px-2.5 py-1 rounded bg-surface-container hover:bg-surface-container-high text-on-surface-variant hover:text-on-surface font-code-sm text-code-sm transition-colors"
              href={runExportUrl(r.run_id, "text")}
              download
            >
              <span className="material-symbols-outlined text-[14px]">
                description
              </span>
              <span>Download Text Report</span>
            </a>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-x-space-md gap-y-1 pt-1 font-code-sm text-code-sm text-on-surface-variant">
          <div className="flex items-center gap-1">
            <span className="text-outline">Prompt version:</span>
            <span className="text-on-surface font-medium">
              {r.prompt_version}
            </span>
          </div>
          <span className="text-outline-variant">•</span>
          <div className="flex items-center gap-1">
            <span className="text-outline">Cases:</span>
            <span className="text-on-surface">{r.case_count}</span>
          </div>
          <span className="text-outline-variant">•</span>
          <div className="flex items-center gap-1">
            <span className="text-outline">Executed:</span>
            <span className="text-on-surface">{formatTimestamp(r.timestamp)}</span>
          </div>
          <span className="text-outline-variant">•</span>
          <div className="flex items-center gap-1">
            <span className="text-outline">Config:</span>
            <span className="text-primary font-mono break-all">
              {r.eval_config_hash
                ? r.eval_config_hash.slice(0, 12)
                : "unavailable"}
            </span>
          </div>
          {r.baseline_run_id && (
            <>
              <span className="text-outline-variant">•</span>
              <div className="flex items-center gap-1">
                <span className="text-outline">Baseline:</span>
                <Link
                  to={`/runs/${r.baseline_run_id}`}
                  className="text-primary font-mono"
                >
                  {r.baseline_run_id.slice(0, 8)}
                </Link>
              </div>
            </>
          )}
        </div>
      </div>

      {/* Diagnostic workbench */}
      <div className="p-gutter flex flex-col gap-space-md">
        {/* Row 1: headline metrics + regression evidence */}
        <div className="grid grid-cols-1 xl:grid-cols-12 gap-space-md">
          <div className="xl:col-span-6 grid grid-cols-2 sm:grid-cols-4 gap-space-xs bg-surface-container-lowest p-space-xs rounded">
            <div className="flex flex-col justify-between p-space-sm rounded bg-surface-container-low">
              <span className="font-label-sm text-label-sm text-outline uppercase">
                Category accuracy
              </span>
              <span className="mt-2 font-code-lg font-semibold text-on-surface">
                {pct(r.category_accuracy)}
              </span>
              <span className="font-code-sm text-code-sm text-outline mt-1">
                {r.case_count} cases
              </span>
            </div>
            <div className="flex flex-col justify-between p-space-sm rounded bg-surface-container-low">
              <span className="font-label-sm text-label-sm text-outline uppercase">
                Macro F1
              </span>
              <span className="mt-2 font-code-lg font-semibold text-on-surface">
                {classification ? classification.macro_f1.toFixed(3) : "—"}
              </span>
              <span className="font-code-sm text-code-sm text-outline mt-1">
                {classification ? "weighted " + classification.weighted_f1.toFixed(3) : "n/a"}
              </span>
            </div>
            <div className="flex flex-col justify-between p-space-sm rounded bg-surface-container-low">
              <span className="font-label-sm text-label-sm text-outline uppercase">
                Summary score
              </span>
              <span className="mt-2 font-code-lg font-semibold text-on-surface">
                {r.average_summary_score.toFixed(2)}/5
              </span>
              <span className="font-code-sm text-code-sm text-outline mt-1">
                judge mean
              </span>
            </div>
            <div className="flex flex-col justify-between p-space-sm rounded bg-surface-container-low">
              <span className="font-label-sm text-label-sm text-outline uppercase">
                Avg latency
              </span>
              <span className="mt-2 font-code-lg font-semibold text-on-surface">
                {Math.round(r.average_latency_ms)} ms
              </span>
              <span className="font-code-sm text-code-sm text-outline mt-1">
                {r.total_input_tokens} in / {r.total_output_tokens} out
              </span>
            </div>
          </div>

          {/* Gate evaluation summary */}
          <div className="xl:col-span-6 bg-surface-container-lowest p-space-sm rounded flex flex-col justify-between gap-space-xs">
            <div className="flex items-center justify-between pb-1 font-label-sm text-label-sm uppercase tracking-wider text-outline">
              <span className="flex items-center gap-1 font-semibold text-on-surface">
                <span
                  className={`w-1.5 h-1.5 rounded-full ${
                    gateStatus === "PASS"
                      ? "bg-secondary"
                      : gateStatus === "FAIL"
                        ? "bg-error"
                        : "bg-tertiary"
                  }`}
                />
                Quality gate
              </span>
              {r.regression && r.regression.violations.length > 0 ? (
                <span className="text-error font-code-sm text-code-sm">
                  {r.regression.violations.length} violations
                </span>
              ) : (
                <span className="text-secondary font-code-sm text-code-sm">
                  {gateStatus}
                </span>
              )}
            </div>
            {r.regression && r.regression.violations.length > 0 ? (
              <ul className="font-code-sm text-code-sm text-on-surface-variant flex flex-col gap-1">
                {r.regression.violations.map(
                  (
                    violation: Record<string, unknown>,
                    index: number,
                  ) => (
                  <li key={index}>
                    {String(violation.metric ?? "metric")}:{" "}
                    <span className="text-error font-mono">
                      {String(violation.value ?? "")}
                    </span>{" "}
                    vs {String(violation.threshold ?? "")}{" "}
                    ({String(violation.kind ?? "violation")})
                  </li>
                ))}
              </ul>
            ) : (
              <p className="font-body-sm text-body-sm text-on-surface-variant">
                No threshold violations recorded.
              </p>
            )}
            {comparison && (
              <div className="flex flex-wrap items-center gap-x-space-md font-code-sm text-code-sm text-on-surface-variant border-t border-surface-container pt-space-xs">
                <span>
                  Δ accuracy{" "}
                  <span className="text-on-surface font-mono">
                    {(comparison.category_accuracy_delta ?? 0).toFixed(4)}
                  </span>
                </span>
                <span>
                  Δ summary{" "}
                  <span className="text-on-surface font-mono">
                    {(comparison.summary_score_delta ?? 0).toFixed(4)}
                  </span>
                </span>
                <span>
                  Δ latency{" "}
                  <span className="text-on-surface font-mono">
                    {Math.round(comparison.latency_delta_ms ?? 0)} ms
                  </span>
                </span>
              </div>
            )}
            {r.confidence_intervals?.accuracy_interval && (
              <div className="font-code-sm text-code-sm text-outline border-t border-surface-container pt-space-xs">
                Bootstrap{" "}
                <span className="text-on-surface">
                  {r.confidence_intervals.accuracy_interval.resamples}
                </span>{" "}
                resamples @{" "}
                <span className="text-on-surface">
                  {r.confidence_intervals.accuracy_interval.confidence_level}
                </span>{" "}
                CI accuracy{" "}
                <span className="text-on-surface font-mono">
                  {pct(r.confidence_intervals.accuracy_interval.lower_bound)} –{" "}
                  {pct(r.confidence_intervals.accuracy_interval.upper_bound)}
                </span>
              </div>
            )}
          </div>
        </div>

        {/* Row 2: per-class breakdown + drift + incompatibilities */}
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-space-md">
          {classification && (
            <div className="lg:col-span-6 bg-surface-container-lowest p-space-md rounded flex flex-col gap-space-sm overflow-x-auto">
              <div className="flex items-center justify-between font-label-sm text-label-sm text-outline uppercase tracking-wider">
                <span className="text-on-surface font-semibold flex items-center gap-1">
                  <span className="material-symbols-outlined text-[15px] text-secondary">
                    category
                  </span>
                  Per-Class Breakdown
                </span>
                <span className="font-code-sm text-code-sm">
                  {Object.keys(classification.per_class).length} categories
                </span>
              </div>
              <table className="w-full text-left font-code-sm text-code-sm">
                <thead>
                  <tr className="text-outline uppercase text-[10px] border-b border-surface-container-high">
                    <th className="py-1 px-1.5 font-medium">Category</th>
                    <th className="py-1 px-1.5 font-medium text-right">TP</th>
                    <th className="py-1 px-1.5 font-medium text-right">FP</th>
                    <th className="py-1 px-1.5 font-medium text-right">FN</th>
                    <th className="py-1 px-1.5 font-medium text-right">
                      Precision
                    </th>
                    <th className="py-1 px-1.5 font-medium text-right">
                      Recall
                    </th>
                    <th className="py-1 px-1.5 font-medium text-right">F1</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-surface-container-low">
                  {Object.entries(classification.per_class).map(
                    ([category, stats]) => (
                      <tr
                        key={category}
                        className="hover:bg-surface-container-low transition-colors"
                      >
                        <td className="py-1.5 px-1.5 font-medium text-on-surface flex items-center gap-1">
                          <span
                            className={`w-1.5 h-1.5 rounded-full ${
                              stats.f1 >= 0.9
                                ? "bg-secondary"
                                : stats.f1 >= 0.6
                                  ? "bg-tertiary"
                                  : "bg-error"
                            }`}
                          />
                          <span>{category}</span>
                        </td>
                        <td className="py-1.5 px-1.5 text-right text-outline">
                          {stats.tp}
                        </td>
                        <td className="py-1.5 px-1.5 text-right text-outline">
                          {stats.fp}
                        </td>
                        <td className="py-1.5 px-1.5 text-right text-outline">
                          {stats.fn}
                        </td>
                        <td className="py-1.5 px-1.5 text-right text-on-surface">
                          {stats.precision.toFixed(3)}
                        </td>
                        <td className="py-1.5 px-1.5 text-right text-on-surface">
                          {stats.recall.toFixed(3)}
                        </td>
                        <td className="py-1.5 px-1.5 text-right font-medium text-on-surface">
                          {stats.f1.toFixed(3)}
                        </td>
                      </tr>
                    ),
                  )}
                </tbody>
              </table>
              <div className="font-code-sm text-code-sm text-outline pt-1">
                Macro F1 {classification.macro_f1.toFixed(3)} · weighted{" "}
                {classification.weighted_f1.toFixed(3)}
              </div>
            </div>
          )}

          {/* Drift panel (when a baseline comparison exists) */}
          <div
            className={`${
              classification ? "lg:col-span-3" : "lg:col-span-6"
            } bg-surface-container-lowest p-space-md rounded flex flex-col gap-space-sm`}
          >
            <div className="flex items-center justify-between font-label-sm text-label-sm text-outline uppercase tracking-wider">
              <span className="text-on-surface font-semibold flex items-center gap-1">
                <span className="material-symbols-outlined text-[15px] text-primary">
                  speed
                </span>
                Category Drift
              </span>
            </div>
            {drift && drift.sufficient_data ? (
              <div className="flex flex-col gap-1 font-code-sm text-code-sm">
                <div className="flex items-center justify-between">
                  <span className="text-outline">Total variation</span>
                  <span className="text-on-surface font-mono font-medium">
                    {drift.total_variation_distance == null
                      ? "—"
                      : drift.total_variation_distance.toFixed(4)}
                  </span>
                </div>
                <div className="mt-space-xs text-outline uppercase text-[10px] font-label-sm tracking-wider">
                  Distribution (baseline → current share)
                </div>
                {Object.entries(
                  drift.baseline_distribution ?? {},
                ).map(([category, share]) => (
                  <div
                    key={category}
                    className="flex items-center justify-between"
                  >
                    <span className="text-on-surface truncate">{category}</span>
                    <span className="text-outline font-mono">
                      {pct(share)} → {pct(drift.current_distribution?.[category] ?? 0)}
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <EmptyState>
                {drift
                  ? "Not enough data on one side for a drift measure."
                  : "No drift evidence: baseline missing or incompatible."}
              </EmptyState>
            )}
          </div>

          {/* Cost/telemetry */}
          <div
            className={`${
              classification ? "lg:col-span-3" : "lg:col-span-6"
            } bg-surface-container-lowest p-space-md rounded flex flex-col justify-between gap-space-sm`}
          >
            <div className="flex items-center justify-between font-label-sm text-label-sm text-outline uppercase tracking-wider">
              <span className="text-on-surface font-semibold flex items-center gap-1">
                <span className="material-symbols-outlined text-[15px] text-primary">
                  payments
                </span>
                Tokens & Cost
              </span>
              <span className="font-code-sm text-code-sm text-secondary font-mono">
                {r.total_cost_usd === null ? "not tracked" : `$${r.total_cost_usd.toFixed(4)}`}
              </span>
            </div>
            <div className="flex flex-col gap-1 font-code-sm text-code-sm">
              <div className="flex items-center justify-between text-outline">
                <span>Total input tokens</span>
                <span className="text-on-surface font-mono">
                  {r.total_input_tokens}
                </span>
              </div>
              <div className="flex items-center justify-between text-outline">
                <span>Total output tokens</span>
                <span className="text-on-surface font-mono">
                  {r.total_output_tokens}
                </span>
              </div>
            </div>
          </div>
        </div>

        {/* Incompatibility + case-inspector band */}
        {r.regression && r.regression.incompatibility_reasons.length > 0 && (
          <Panel title="Comparison incompatibilities">
            <ul className="font-body-sm text-body-sm text-on-surface-variant list-disc pl-5">
              {r.regression.incompatibility_reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          </Panel>
        )}

        <CaseInspector report={r} />
      </div>
    </div>
  );
}

function CaseInspector({ report }: { report: EvaluationReport }) {
  const [filter, setFilter] = useState<
    "all" | "failures" | "matches" | "near"
  >("all");
  const [category, setCategory] = useState("all");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [openCase, setOpenCase] = useState<string | null>(null);
  const PAGE_SIZE = 25;

  const categories = useMemo(
    () => Array.from(new Set(report.results.map((x) => x.expected_category))),
    [report.results],
  );

  const visible = useMemo(() => {
    const query = search.trim().toLowerCase();
    return report.results.filter((result) => {
      if (filter === "failures" && result.category_match === true && result.summary_score >= 3.0) {
        return false;
      }
      if (filter === "matches" && !result.category_match) {
        return false;
      }
      if (filter === "near" && result.summary_score >= 3.0) {
        return false;
      }
      if (category !== "all" && result.expected_category !== category) {
        return false;
      }
      if (!query) {
        return true;
      }
      const haystack = `${result.test_case_id} ${result.expected_category} ${result.actual_category} ${result.expected_summary} ${result.actual_summary}`;
      return haystack.toLowerCase().includes(query);
    });
  }, [report.results, filter, category, search]);

  const pageCount = Math.max(1, Math.ceil(visible.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount - 1);

  return (
    <div className="bg-surface-container-lowest rounded flex flex-col">
      {/* Filter bar */}
      <div className="p-space-sm bg-surface-container-low flex flex-wrap items-center justify-between gap-space-sm rounded-t">
        <div className="flex flex-wrap items-center gap-space-xs">
          <span className="font-label-sm text-label-sm text-outline uppercase mr-1">
            Filter:
          </span>
          {(
            [
              ["all", `All (${report.results.length})`, ""],
              ["failures", `Failures (${report.results.filter((x) => !x.category_match).length})`, "text-error"],
              ["matches", `Matches (${report.results.filter((x) => x.category_match).length})`, "text-secondary"],
            ] as const
          ).map(([key, label, tone]) => (
            <button
              key={key}
              className={`${
                filter === key ? "bg-surface-container-high" : "bg-surface-container"
              } px-2.5 py-1 rounded font-code-sm text-code-sm transition-colors ${
                filter === key ? "text-primary font-medium" : tone || "text-on-surface-variant"
              } hover:bg-surface-container-high`}
              onClick={() => {
                setFilter(key);
                setPage(0);
              }}
            >
              {label}
            </button>
          ))}
          <select
            className="appearance-none ml-2 bg-surface-container text-on-surface font-code-sm text-code-sm py-1 pl-2 pr-6 rounded focus:outline-none cursor-pointer hover:bg-surface-container-high"
            value={category}
            onChange={(e) => {
              setCategory(e.target.value);
              setPage(0);
            }}
          >
            <option value="all">All Categories</option>
            {categories.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </div>
        <div className="flex items-center gap-space-xs w-full sm:w-auto">
          <div className="relative flex-1 sm:w-64">
            <span className="material-symbols-outlined text-[14px] text-outline absolute left-2 top-2">
              search
            </span>
            <input
              className="w-full bg-surface-container pl-7 pr-3 py-1 rounded font-code-sm text-code-sm text-on-surface placeholder:text-outline focus:outline-none focus:bg-surface-bright"
              placeholder="Search prompt text, case ID…"
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setPage(0);
              }}
            />
          </div>
          <span className="font-code-sm text-code-sm text-outline whitespace-nowrap">
            Showing {visible.length} of {report.results.length}
          </span>
        </div>
      </div>

      {/* Table headers */}
      <div className="grid grid-cols-12 px-space-md py-1.5 bg-surface-container font-label-sm text-label-sm text-outline uppercase tracking-wider">
        <div className="col-span-2">Case ID</div>
        <div className="col-span-2">Category</div>
        <div className="col-span-2">Expected</div>
        <div className="col-span-2">Predicted</div>
        <div className="col-span-1 text-right">Score</div>
        <div className="col-span-1 text-right">Latency</div>
        <div className="col-span-2 text-right">Status</div>
      </div>

      {/* Rows */}
      <div className="flex flex-col divide-y divide-surface-container-high">
        {visible
          .slice(safePage * PAGE_SIZE, (safePage + 1) * PAGE_SIZE)
          .map((result) => {
            const open = openCase === result.test_case_id;
            return (
              <div key={result.test_case_id} className="flex flex-col hover:bg-surface-container-low transition-colors">
                <button
                  type="button"
                  className="grid grid-cols-12 items-center px-space-md py-2 cursor-pointer text-left w-full"
                  onClick={() => setOpenCase(open ? null : result.test_case_id)}
                >
                  <div className="col-span-2 flex items-center gap-2 font-code-sm text-code-sm">
                    <span
                      className={`material-symbols-outlined text-[16px] text-outline transition-transform duration-150 ${open ? "rotate-90" : ""}`}
                    >
                      chevron_right
                    </span>
                    <span
                      className={`font-medium font-mono ${result.category_match ? "text-on-surface" : "text-error"}`}
                    >
                      {result.test_case_id}
                    </span>
                    <span
                      className={`px-1 py-0.5 rounded text-[10px] font-bold font-label-sm text-label-sm ${
                        result.category_match
                          ? "bg-secondary/10 text-secondary"
                          : "bg-error-container/30 text-error"
                      }`}
                    >
                      {result.category_match ? "MATCH" : "MISMATCH"}
                    </span>
                  </div>
                  <div className="col-span-2 font-code-sm text-code-sm text-on-surface-variant truncate">
                    {result.expected_category}
                  </div>
                  <div className="col-span-2 font-code-sm text-code-sm">
                    <span className="px-1.5 py-0.5 rounded bg-secondary/10 text-secondary font-mono text-[11px]">
                      {result.expected_category}
                    </span>
                  </div>
                  <div className="col-span-2 font-code-sm text-code-sm">
                    <span
                      className={`px-1.5 py-0.5 rounded font-mono text-[11px] ${
                        result.category_match
                          ? "bg-secondary/10 text-secondary"
                          : "bg-error/10 text-error"
                      }`}
                    >
                      {result.actual_category}
                    </span>
                  </div>
                  <div className="col-span-1 text-right font-code-sm text-code-sm text-on-surface font-mono">
                    {result.summary_score.toFixed(1)}
                  </div>
                  <div className="col-span-1 text-right font-code-sm text-code-sm text-outline font-mono">
                    {Math.round(result.latency_ms)} ms
                  </div>
                  <div className="col-span-2 text-right flex items-center justify-end gap-2">
                    <span
                      className={`px-1.5 py-0.5 rounded text-label-sm text-label-sm font-semibold ${
                        result.category_match
                          ? "bg-secondary/10 text-secondary"
                          : "bg-error-container/20 text-error"
                      }`}
                    >
                      {result.category_match ? "PASSED" : "FAILED"}
                    </span>
                  </div>
                </button>
                {open && (
                  <div className="px-space-md py-space-sm bg-surface-container-lowest flex flex-col gap-space-sm">
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-space-md">
                      <div className="flex flex-col gap-1 p-space-sm rounded bg-surface-container-low">
                        <div className="flex items-center justify-between font-label-sm text-label-sm text-outline uppercase">
                          <span>Expected summary</span>
                          <span className="font-code-sm text-[10px] text-primary">
                            {result.expected_category}
                          </span>
                        </div>
                        <div className="bg-surface-container p-space-sm rounded font-code-sm text-code-sm text-on-surface overflow-x-auto">
                          <pre className="whitespace-pre-wrap break-words">
                            {result.expected_summary}
                          </pre>
                        </div>
                      </div>
                      <div className="flex flex-col gap-1 p-space-sm rounded bg-surface-container-low">
                        <div className="flex items-center justify-between font-label-sm text-label-sm text-outline uppercase">
                          <span>Model response (judge verdict)</span>
                          <span
                            className={`px-1 py-0.5 rounded font-code-sm text-[10px] font-semibold ${
                              result.category_match
                                ? "bg-secondary/10 text-secondary"
                                : "bg-error-container/30 text-error"
                            }`}
                          >
                            {result.category_match ? "MATCH" : "MISMATCH"}
                          </span>
                        </div>
                        <div className="bg-surface-container p-space-sm rounded font-code-sm text-code-sm text-on-surface overflow-x-auto">
                          <pre className="whitespace-pre-wrap break-words">
                            {result.actual_summary}
                          </pre>
                        </div>
                        <div className="flex flex-wrap items-center justify-between gap-space-xs font-code-sm text-[11px] text-outline px-1">
                          <span>Tokens in: {result.input_tokens}</span>
                          <span>Tokens out: {result.output_tokens}</span>
                          <span className="text-on-surface-variant">
                            Score {result.summary_score.toFixed(1)}/5
                          </span>
                        </div>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        {visible.length === 0 && (
          <div className="p-space-md">
            <EmptyState>No cases match the current filters.</EmptyState>
          </div>
        )}
      </div>

      {/* Pagination */}
      <div className="p-space-sm bg-surface-container-low rounded-b flex items-center justify-between font-code-sm text-code-sm text-outline">
        <div className="flex items-center gap-2">
          <span className="font-medium text-on-surface">25</span>
          <span className="text-outline-variant">|</span>
          <span>Records {safePage + 1}–{Math.min((safePage + 1) * PAGE_SIZE, visible.length)} of {visible.length}</span>
        </div>
        <div className="flex items-center gap-1">
          <button
            className="px-2 py-1 rounded bg-surface-container text-outline hover:text-on-surface disabled:opacity-50"
            disabled={safePage === 0}
            onClick={() => setPage(safePage - 1)}
          >
            <span className="material-symbols-outlined text-[14px]">
              chevron_left
            </span>
          </button>
          <span className="px-2 py-0.5 text-on-surface font-medium">
            Page {safePage + 1} of {pageCount}
          </span>
          <button
            className="px-2 py-1 rounded bg-surface-container text-on-surface hover:bg-surface-container-high disabled:opacity-50"
            disabled={safePage >= pageCount - 1}
            onClick={() => setPage(safePage + 1)}
          >
            <span className="material-symbols-outlined text-[14px]">
              chevron_right
            </span>
          </button>
        </div>
      </div>
    </div>
  );
}
