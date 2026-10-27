import { useCallback, useEffect, useRef, useState } from "react";
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
} from "../api/client";
import { TERMINAL_JOB_STATUSES } from "../api/client";
import type {
  BaselineListResponse,
  RunCreateRequest,
  RunDetailResponse,
  RunJobStatusResponse,
  RunListResponse,
} from "../api/types";
import {
  EmptyState,
  ErrorBanner,
  Loading,
  Metric,
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
};

type NumberFields = Pick<
  RunCreateRequest,
  | "limit"
  | "min_category_accuracy"
  | "min_summary_score"
  | "max_latency_ms"
  | "max_category_accuracy_drop"
  | "max_summary_score_drop"
>;

function toNumber(value: string): number | undefined {
  const trimmed = value.trim();
  if (!trimmed) {
    return undefined;
  }
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : undefined;
}

export function RunsPage() {
  const [runs, setRuns] = useState<RunListResponse | null>(null);
  const [baselines, setBaselines] = useState<BaselineListResponse | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
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

  const pendingNumbers: NumberFields = {
    limit: toNumber(form.limit),
    min_category_accuracy: toNumber(form.minCategoryAccuracy),
    min_summary_score: toNumber(form.minSummaryScore),
    max_latency_ms: toNumber(form.maxLatencyMs),
    max_category_accuracy_drop: toNumber(form.maxCategoryAccuracyDrop),
    max_summary_score_drop: toNumber(form.maxSummaryScoreDrop),
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
    <div>
      <h1>Evaluation runs</h1>
      <ErrorBanner error={error} />
      {loading && <Loading />}

      {runs && !loading && (
        <section className="panel">
          <h2>Run history</h2>
          {runs.items.length === 0 ? (
            <EmptyState>
              No persisted runs yet. {runs.skipped_runs.length > 0
                ? `(${runs.skipped_runs.length} run files were skipped as unreadable — see the API for diagnostics.)`
                : ""}{" "}
              Use the run form below to create the first one.
            </EmptyState>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Run</th>
                  <th>When</th>
                  <th>Model</th>
                  <th>Dataset</th>
                  <th>Cases</th>
                  <th>Accuracy</th>
                  <th>Summary</th>
                  <th>Latency</th>
                  <th>Gate</th>
                  <th>Baseline</th>
                </tr>
              </thead>
              <tbody>
                {runs.items.map((run) => (
                  <tr key={run.run_id}>
                    <td>
                      <Link to={`/runs/${run.run_id}`}>
                        <span className="mono">{run.run_id}</span>
                      </Link>
                    </td>
                    <td>{formatTimestamp(run.timestamp)}</td>
                    <td className="mono">{run.model}</td>
                    <td>{run.dataset}</td>
                    <td>{run.case_count}</td>
                    <td>{pct(run.category_accuracy)}</td>
                    <td>{run.average_summary_score.toFixed(2)}/5</td>
                    <td>{Math.round(run.average_latency_ms)} ms</td>
                    <td>
                      <StatusBadge status={run.regression_status} />
                    </td>
                    <td>
                      {run.baseline_run_id ? (
                        <Link to={`/runs/${run.baseline_run_id}`}>
                          <span className="mono">{run.baseline_run_id}</span>
                        </Link>
                      ) : (
                        "—"
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <p>
            <a href={runExportUrl(runs.items[0]?.run_id ?? "", "json")} download>
              Export latest as JSON
            </a>{" "}
            ·{" "}
            <a href={runExportUrl(runs.items[0]?.run_id ?? "", "text")} download>
              as text
            </a>
          </p>
        </section>
      )}

      <section className="panel">
        <h2>Queue a run</h2>
        {runs && runs.total === 0 && baselines && baselines.total === 0 && (
          <p className="notes-list">
            No baselines exist; the run will report without a comparison.
          </p>
        )}
        {!confirming && !job && (
          <form
            onSubmit={(event) => {
              event.preventDefault();
              setConfirming(true);
            }}
          >
            <div className="field-row">
              <div className="field">
                <label htmlFor="prompt-version">Prompt version</label>
                <input
                  id="prompt-version"
                  value={form.promptVersion}
                  onChange={(e) =>
                    setForm({ ...form, promptVersion: e.target.value })
                  }
                />
              </div>
              <div className="field">
                <label htmlFor="limit">Limit (first N cases)</label>
                <input
                  id="limit"
                  value={form.limit}
                  placeholder="all"
                  onChange={(e) => setForm({ ...form, limit: e.target.value })}
                />
              </div>
              <div className="field">
                <label htmlFor="baseline">Baseline run id (or “latest”)</label>
                <input
                  id="baseline"
                  value={form.baseline}
                  onChange={(e) =>
                    setForm({ ...form, baseline: e.target.value })
                  }
                />
              </div>
            </div>
            <div className="field-row">
              <div className="field">
                <label htmlFor="min-acc">Min category accuracy</label>
                <input
                  id="min-acc"
                  value={form.minCategoryAccuracy}
                  placeholder="unset"
                  onChange={(e) =>
                    setForm({ ...form, minCategoryAccuracy: e.target.value })
                  }
                />
              </div>
              <div className="field">
                <label htmlFor="min-score">Min summary score</label>
                <input
                  id="min-score"
                  value={form.minSummaryScore}
                  placeholder="unset"
                  onChange={(e) =>
                    setForm({ ...form, minSummaryScore: e.target.value })
                  }
                />
              </div>
              <div className="field">
                <label htmlFor="max-latency">Max latency (ms)</label>
                <input
                  id="max-latency"
                  value={form.maxLatencyMs}
                  placeholder="unset"
                  onChange={(e) =>
                    setForm({ ...form, maxLatencyMs: e.target.value })
                  }
                />
              </div>
            </div>
            <div className="field-row">
              <div className="field">
                <label htmlFor="acc-drop">Max accuracy drop</label>
                <input
                  id="acc-drop"
                  value={form.maxCategoryAccuracyDrop}
                  placeholder="unset"
                  onChange={(e) =>
                    setForm({
                      ...form,
                      maxCategoryAccuracyDrop: e.target.value,
                    })
                  }
                />
              </div>
              <div className="field">
                <label htmlFor="score-drop">Max summary score drop</label>
                <input
                  id="score-drop"
                  value={form.maxSummaryScoreDrop}
                  placeholder="unset"
                  onChange={(e) =>
                    setForm({
                      ...form,
                      maxSummaryScoreDrop: e.target.value,
                    })
                  }
                />
              </div>
            </div>
            <p className="notes-list">
              Blank thresholds stay unset; an empty dataset of gates produces
              an INCONCLUSIVE gate by design.
            </p>
            <button type="submit" className="primary">
              Review and submit
            </button>
          </form>
        )}

        {confirming && (
          <div>
            <h2>Confirm run request</h2>
            <ul className="notes-list">
              <li>Prompt version: {payload.prompt_version}</li>
              <li>
                Limit: {payload.limit ?? "full dataset"}
              </li>
              <li>
                Baseline: {payload.baseline_run_id ?? "none (no comparison)"}
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
            <button
              type="button"
              className="primary"
              onClick={() => void submit()}
              disabled={submitting}
            >
              {submitting ? "Submitting…" : "Confirm and queue"}
            </button>{" "}
            <button type="button" onClick={() => setConfirming(false)}>
              Back to form
            </button>
          </div>
        )}

        {job && (
          <div>
            <h2>Run status</h2>
            {duplicateNote && <p className="notes-list">{duplicateNote}</p>}
            <div className="grid">
              <Metric
                label="Job"
                value={job.job_id}
                mono
              />
              <Metric label="Status" value={job.status} />
              <Metric
                label="Report"
                value={job.report_run_id ?? "—"}
                mono
              />
            </div>
            {job.error_message && (
              <div className="error-banner">
                <span className="code">{job.error_code}</span>
                {job.error_message}
              </div>
            )}
            {!TERMINAL_JOB_STATUSES.has(job.status) && (
              <button type="button" onClick={() => void cancel()}>
                Cancel run
              </button>
            )}
            {TERMINAL_JOB_STATUSES.has(job.status) &&
              job.status === "quality_gate_failed" && (
              <p className="notes-list">
                The run completed but failed the quality gate; the persisted
                report still carries the full evidence.
              </p>
            )}
          </div>
        )}
      </section>
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

  return (
    <div>
      <h1>Run {r.run_id}</h1>
      <p>
        {formatTimestamp(r.timestamp)} · {r.dataset} · prompt {r.prompt_version}
        {" · "}
        <a href={runExportUrl(r.run_id, "json")} download>
          export JSON
        </a>{" "}
        · <a href={runExportUrl(r.run_id, "text")} download>export text</a>
      </p>

      <section className="grid">
        <Metric label="Gate" value={r.regression?.status ?? "INCONCLUSIVE"} />
        <Metric label="Case accuracy" value={pct(r.category_accuracy)} />
        <Metric
          label="Summary score"
          value={`${r.average_summary_score.toFixed(2)}/5`}
        />
        <Metric label="Cases" value={String(r.case_count)} />
        <Metric
          label="Latency"
          value={`${Math.round(r.average_latency_ms)} ms`}
        />
        <Metric
          label="Tokens"
          value={`${r.total_input_tokens} in / ${r.total_output_tokens} out`}
        />
        {r.total_cost_usd !== null && (
          <Metric label="Cost" value={`$${r.total_cost_usd.toFixed(4)}`} />
        )}
      </section>

      {r.regression && r.regression.violations.length > 0 && (
        <section className="panel">
          <h2>Threshold violations</h2>
          <ul className="notes-list">
            {r.regression.violations.map((violation, index) => (
              <li key={index}>
                {String(violation.metric ?? "metric")}:{" "}
                {String(violation.value ?? "")} vs{" "}
                {String(violation.threshold ?? "")} (
                {String(violation.kind ?? "violation")})
              </li>
            ))}
          </ul>
        </section>
      )}

      {r.regression && r.regression.incompatibility_reasons.length > 0 && (
        <section className="panel">
          <h2>Comparison incompatibilities</h2>
          <ul className="notes-list">
            {r.regression.incompatibility_reasons.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </section>
      )}

      {comparison && (
        <section className="panel">
          <h2>Baseline comparison</h2>
          <div className="grid">
            <Metric
              label="Baseline"
              value={comparison.baseline_run_id ?? "—"}
              mono
            />
            <Metric
              label="Δ accuracy"
              value={(comparison.category_accuracy_delta ?? 0).toFixed(4)}
            />
            <Metric
              label="Δ summary"
              value={(comparison.summary_score_delta ?? 0).toFixed(4)}
            />
            <Metric
              label="Δ latency"
              value={`${Math.round(comparison.latency_delta_ms ?? 0)} ms`}
            />
          </div>
        </section>
      )}

      {r.confidence_intervals && (
        <section className="panel">
          <h2>Bootstrap confidence intervals</h2>
          <div className="grid">
            <Metric
              label="Accuracy CI"
              value={`${pct(r.confidence_intervals.category_accuracy.lower)} – ${pct(r.confidence_intervals.category_accuracy.upper)}`}
            />
            <Metric
              label="Summary CI"
              value={`${r.confidence_intervals.average_summary_score.lower.toFixed(2)} – ${r.confidence_intervals.average_summary_score.upper.toFixed(2)}`}
            />
            <Metric
              label="Bootstrap"
              value={`${r.confidence_intervals.resamples} resamples @ ${r.confidence_intervals.confidence_level}`}
            />
          </div>
        </section>
      )}

      {classification && (
        <section className="panel">
          <h2>
            Classification (accuracy {pct(classification.accuracy)}, macro F1{" "}
            {classification.macro_f1.toFixed(3)})
          </h2>
          <table>
            <thead>
              <tr>
                <th>Category</th>
                <th>Support</th>
                <th>Precision</th>
                <th>Recall</th>
                <th>F1</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(classification.categories).map(
                ([category, stats]) => (
                  <tr key={category}>
                    <td className="mono">{category}</td>
                    <td>{stats.support}</td>
                    <td>{stats.precision.toFixed(2)}</td>
                    <td>{stats.recall.toFixed(2)}</td>
                    <td>{stats.f1.toFixed(2)}</td>
                  </tr>
                ),
              )}
            </tbody>
          </table>
        </section>
      )}

      <section className="panel">
        <h2>Per-case results</h2>
        <table>
          <thead>
            <tr>
              <th>Case</th>
              <th>Expected</th>
              <th>Actual</th>
              <th>Match</th>
              <th>Score</th>
              <th>Latency</th>
            </tr>
          </thead>
          <tbody>
            {r.results.map((result) => (
              <tr key={result.test_case_id}>
                <td className="mono">{result.test_case_id}</td>
                <td>{result.expected_category}</td>
                <td>{result.actual_category}</td>
                <td>{result.category_match ? "✓" : "✗"}</td>
                <td>{result.summary_score.toFixed(1)}</td>
                <td>{Math.round(result.latency_ms)} ms</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
