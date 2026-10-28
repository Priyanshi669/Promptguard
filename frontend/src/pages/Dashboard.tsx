import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  ApiError,
  calibrationOverview,
  getRun,
  listRuns,
  readiness,
} from "../api/client";
import type {
  ClassificationMetrics,
  DraftOverviewResponse,
  EvaluationReport,
  ReadinessResponse,
  RunDetailResponse,
  RunListItem,
  RunListResponse,
} from "../api/types";
import {
  EmptyState,
  ErrorBanner,
  GateDot,
  Loading,
  Panel,
  StatusBadge,
  formatTimestamp,
  pct,
} from "../components/ui";

interface OverviewData {
  readiness: ReadinessResponse;
  runs: RunListResponse;
  latest: { item: RunListItem; report: EvaluationReport } | null;
  draft: DraftOverviewResponse | null;
}

function loadOverview(): Promise<OverviewData> {
  return (async () => {
    const [readinessRes, runsRes] = await Promise.all([
      readiness(),
      listRuns(0, 5),
    ]);
    const items = runsRes.items;
    let latest: { item: RunListItem; report: EvaluationReport } | null = null;
    if (items.length > 0) {
      const head = items[0]!;
      try {
        const detail: RunDetailResponse = await getRun(head.run_id);
        latest = { item: head, report: detail.report };
      } catch (err) {
        // The list entry still carries the headline metrics; the
        // classification-required panels degrade to "unavailable".
        if (!(err instanceof ApiError) || err.status !== 404) {
          throw err;
        }
      }
    }
    let draft: DraftOverviewResponse | null = null;
    try {
      draft = await calibrationOverview();
    } catch (err) {
      if (!(err instanceof ApiError && err.code === "draft_not_found")) {
        throw err;
      }
    }
    return { readiness: readinessRes, runs: runsRes, latest, draft };
  })();
}

function MacroF1({ classification }: { classification: ClassificationMetrics | null | undefined }) {
  if (!classification) {
    return <span className="text-outline font-code-sm text-code-sm">unavailable</span>;
  }
  return (
    <span className="font-code-lg font-semibold text-on-surface leading-none">
      {classification.macro_f1.toFixed(3)}
    </span>
  );
}

export function Dashboard() {
  const [data, setData] = useState<OverviewData | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await loadOverview());
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const ready = data?.readiness.ready ?? false;
  const latest = data?.latest ?? null;

  return (
    <div className="flex flex-col w-full">
      {/* Workspace sub-header */}
      <div className="w-full bg-surface-container-lowest px-gutter py-space-sm flex flex-wrap items-center justify-between gap-space-md">
        <div className="flex items-center gap-1.5 font-code-sm text-code-sm text-outline">
          <span className="text-on-surface font-medium">Overview</span>
        </div>
        <div className="flex items-center gap-1.5">
          <span
            className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-surface-container font-code-sm text-code-sm ${
              ready ? "text-secondary" : "text-error"
            }`}
          >
            <span
              className={`w-1.5 h-1.5 rounded-full ${ready ? "bg-secondary" : "bg-error"}`}
            />
            <span>API {ready ? "READY" : "UNREADY"}</span>
          </span>
          <Link
            className="flex items-center gap-1 h-7 px-space-md rounded bg-on-surface hover:bg-primary-fixed text-surface-container-lowest font-headline-sm text-body-sm font-semibold transition-colors"
            to="/runs"
          >
            <span className="material-symbols-outlined text-[14px]">add</span>
            <span>New Run</span>
          </Link>
        </div>
      </div>

      <div className="w-full p-gutter flex flex-col gap-gutter">
        <ErrorBanner error={error} />
        {loading && <Loading />}

        {data && (
          <>
            {/* Top metrics row: all values come from persisted runs */}
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-space-sm">
              <div className="bg-surface-container-low p-space-md rounded flex flex-col justify-between">
                <div className="flex items-center justify-between text-outline">
                  <span className="font-label-sm text-label-sm uppercase tracking-wider">
                    Evaluation runs
                  </span>
                  <span className="material-symbols-outlined text-[15px]">
                    model_training
                  </span>
                </div>
                <div className="mt-space-xs flex items-baseline gap-space-sm">
                  <span className="font-code-lg text-headline-sm font-semibold text-on-surface">
                    {data.runs.total}
                  </span>
                </div>
                <div className="mt-space-xs font-code-sm text-code-sm text-outline flex items-center justify-between">
                  <span>persisted reports</span>
                  <span className="text-on-surface-variant">
                    {data.runs.skipped_runs.length} skipped
                  </span>
                </div>
              </div>

              <div className="bg-surface-container-low p-space-md rounded flex flex-col justify-between">
                <div className="flex items-center justify-between text-outline">
                  <span className="font-label-sm text-label-sm uppercase tracking-wider">
                    Classification accuracy
                  </span>
                  <span className="material-symbols-outlined text-[15px]">
                    verified
                  </span>
                </div>
                <div className="mt-space-xs flex items-baseline gap-space-sm">
                  <span className="font-code-lg text-headline-sm font-semibold text-on-surface">
                    {latest ? pct(latest.item.category_accuracy) : "—"}
                  </span>
                </div>
                <div className="mt-space-xs font-code-sm text-code-sm text-outline flex items-center justify-between">
                  <span>latest run</span>
                  {latest?.report.confidence_intervals?.accuracy_interval ? (
                    <span className="text-on-surface-variant">
                      CI{" "}
                      {pct(latest.report.confidence_intervals.accuracy_interval.lower_bound)}
                      –
                      {pct(latest.report.confidence_intervals.accuracy_interval.upper_bound)}
                    </span>
                  ) : (
                    <span className="text-on-surface-variant">
                      {latest ? "no CI" : "none yet"}
                    </span>
                  )}
                </div>
              </div>

              <div className="bg-surface-container-low p-space-md rounded flex flex-col justify-between">
                <div className="flex items-center justify-between text-outline">
                  <span className="font-label-sm text-label-sm uppercase tracking-wider">
                    Macro F1
                  </span>
                  <span className="material-symbols-outlined text-[15px]">
                    insights
                  </span>
                </div>
                <div className="mt-space-xs flex items-baseline gap-space-sm">
                  <MacroF1 classification={latest?.report.classification} />
                </div>
                <div className="mt-space-xs font-code-sm text-code-sm text-outline flex items-center justify-between">
                  <span>latest run</span>
                  <span className="text-on-surface-variant font-mono">
                    {latest?.item.dataset ?? "—"}
                  </span>
                </div>
              </div>

              <div className="bg-surface-container-low p-space-md rounded flex flex-col justify-between">
                <div className="flex items-center justify-between text-outline">
                  <span className="font-label-sm text-label-sm uppercase tracking-wider">
                    Average latency
                  </span>
                  <span className="material-symbols-outlined text-[15px]">
                    timer
                  </span>
                </div>
                <div className="mt-space-xs flex items-baseline gap-space-sm">
                  <span className="font-code-lg text-headline-sm font-semibold text-on-surface">
                    {latest
                      ? `${Math.round(latest.item.average_latency_ms)} ms`
                      : "—"}
                  </span>
                </div>
                <div className="mt-space-xs font-code-sm text-code-sm text-outline flex items-center justify-between">
                  <span>latest run</span>
                  <span className="text-on-surface-variant">
                    {latest
                      ? `${latest.item.case_count} cases`
                      : "no runs yet"}
                  </span>
                </div>
              </div>
            </div>

            {/* Operational split: runs table + telemetry */}
            <div className="w-full flex flex-col lg:flex-row gap-space-md items-start">
              <div className="w-full lg:w-[70%] bg-surface-container-lowest rounded overflow-hidden flex flex-col">
                <div className="px-space-md py-space-sm bg-surface-container-low flex flex-wrap items-center justify-between gap-space-sm">
                  <div className="flex items-center gap-space-sm">
                    <span className="font-headline-sm text-headline-sm text-on-surface">
                      Evaluation Runs
                    </span>
                  </div>
                  <span className="font-code-sm text-code-sm px-1.5 py-0.5 rounded bg-surface-container text-on-surface-variant">
                    {data.runs.total} total
                  </span>
                </div>
                <div className="w-full overflow-x-auto">
                  {data.runs.items.length === 0 ? (
                    <div className="p-space-md">
                      <div className="empty">
                        No evaluation runs persisted yet. Queue one from
                        Evaluation runs once a provider key is configured.
                      </div>
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
                          <th className="px-space-md font-medium text-center">
                            Gate
                          </th>
                          <th className="px-space-md font-medium text-right">
                            Action
                          </th>
                        </tr>
                      </thead>
                      <tbody className="font-code-sm text-code-sm divide-y divide-surface-container">
                        {data.runs.items.map((run) => (
                          <tr
                            key={run.run_id}
                            className="hover:bg-surface-container transition-colors"
                          >
                            <td className="px-space-md py-space-sm whitespace-nowrap">
                              <div className="flex items-center gap-1.5">
                                <GateDot status={run.regression_status} />
                                <Link
                                  to={`/runs/${run.run_id}`}
                                  className="font-code-md text-code-md text-primary font-medium truncate"
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
                    Showing {data.runs.items.length} of {data.runs.total}{" "}
                    evaluations
                  </span>
                  <Link
                    to="/runs"
                    className="text-primary hover:text-primary-fixed font-code-sm text-code-sm"
                  >
                    All runs →
                  </Link>
                </div>
              </div>

              {/* Telemetry column (real readiness/config fields only) */}
              <div className="w-full lg:w-[30%] flex flex-col gap-space-md">
                <Panel
                  title="Quality-Gate Verdict"
                  actions={
                    latest ? (
                      <Link
                        to={`/runs/${latest.item.run_id}`}
                        className="font-code-sm text-code-sm text-primary font-mono"
                      >
                        run
                      </Link>
                    ) : null
                  }
                >
                  <div className="p-space-sm rounded bg-surface-container-low flex flex-col gap-space-xs">
                    <div className="flex items-center justify-between">
                      <span className="font-label-sm text-label-sm uppercase text-outline">
                        Evaluation Status
                      </span>
                      <StatusBadge status={latest?.item.regression_status ?? null} />
                    </div>
                    <p className="font-body-sm text-body-sm text-on-surface-variant leading-relaxed">
                      Gate computed
                      {latest
                        ? " from the persisted report below the configured thresholds."
                        : " once a run with a baseline and thresholds is persisted."}
                    </p>
                  </div>
                  <div className="flex flex-col divide-y divide-surface-container font-code-sm text-code-sm">
                    {Object.entries(data.readiness.checks).map(
                      ([name, ok]) => (
                        <div key={name} className="py-space-xs flex items-center justify-between">
                          <div className="flex items-center gap-space-xs">
                            <span
                              className={`w-1.5 h-1.5 rounded-full ${ok ? "bg-secondary" : "bg-error"}`}
                            />
                            <span className="text-on-surface">{name}</span>
                          </div>
                          <span
                            className={ok ? "text-secondary" : "text-error font-medium"}
                          >
                            {ok ? "OK" : "FAILING"}
                          </span>
                        </div>
                      ),
                    )}
                    <div className="py-space-xs flex items-center justify-between">
                      <div className="flex items-center gap-space-xs">
                        <span className="material-symbols-outlined text-[13px] text-outline">
                          dns
                        </span>
                        <span className="text-on-surface">Auth mode</span>
                      </div>
                      <span className="text-outline font-mono">
                        {data.readiness.auth_mode}
                      </span>
                    </div>
                  </div>
                </Panel>

                <Panel title="Calibration Draft">
                  {data.draft ? (
                    <div className="flex flex-col font-code-sm text-code-sm">
                      <div className="py-space-xs flex items-center justify-between">
                        <span className="text-on-surface">Scored rows</span>
                        <span className="text-on-surface font-mono">
                          {data.draft.overview.scored_rows}/
                          {data.draft.overview.total_rows}
                        </span>
                      </div>
                      <div className="py-space-xs flex items-center justify-between">
                        <span className="text-on-surface">
                          Pending reviewer rows
                        </span>
                        <span className="text-outline font-mono">
                          {data.draft.overview.pending_reviewer_rows}
                        </span>
                      </div>
                      <div className="py-space-xs flex items-center justify-between">
                        <span className="text-on-surface">Calibration</span>
                        <span className="text-on-surface-variant font-mono">
                          {data.draft.overview.calibration_state}
                        </span>
                      </div>
                      <Link
                        to="/calibration"
                        className="py-space-xs inline-flex items-center gap-1 text-primary hover:text-primary-fixed font-code-sm text-code-sm"
                      >
                        Open calibration workspace
                        <span className="material-symbols-outlined text-[14px]">
                          arrow_forward
                        </span>
                      </Link>
                    </div>
                  ) : (
                    <EmptyState>
                      No calibration draft workspace exists yet.
                    </EmptyState>
                  )}
                </Panel>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
