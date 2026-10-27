import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  ApiError,
  calibrationOverview,
  config,
  datasetSummary,
  listRuns,
  readiness,
} from "../api/client";
import type {
  ConfigResponse,
  DatasetSummaryResponse,
  DraftOverviewResponse,
  ReadinessResponse,
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

export function Dashboard() {
  const [readinessData, setReadinessData] =
    useState<ReadinessResponse | null>(null);
  const [configData, setConfigData] = useState<ConfigResponse | null>(null);
  const [datasetData, setDatasetData] = useState<DatasetSummaryResponse | null>(
    null,
  );
  const [runsData, setRunsData] = useState<RunListResponse | null>(null);
  const [draftData, setDraftData] = useState<DraftOverviewResponse | null>(
    null,
  );
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [readinessRes, configRes, runsRes] = await Promise.all([
        readiness(),
        config(),
        listRuns(0, 5),
      ]);
      setReadinessData(readinessRes);
      setConfigData(configRes);
      setRunsData(runsRes);
      const v1 = await datasetSummary("v1");
      setDatasetData(v1);
      try {
        setDraftData(await calibrationOverview());
      } catch (err) {
        if (err instanceof ApiError && err.code === "draft_not_found") {
          setDraftData(null);
        } else {
          throw err;
        }
      }
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div>
      <h1>Dashboard</h1>
      <ErrorBanner error={error} />
      {loading && <Loading />}
      {readinessData && configData && (
        <>
          <section className="grid">
            <Metric
              label="Readiness"
              value={
                readinessData.ready
                  ? "ready"
                  : `degraded (${Object.entries(readinessData.checks)
                      .filter(([, ok]) => !ok)
                      .map(([name]) => name)
                      .join(", ") || "see issues"})`
              }
            />
            <Metric
              label="Auth mode"
              value={readinessData.auth_mode}
            />
            <Metric
              label="Candidate model"
              value={configData.candidate_model}
              mono
            />
            <Metric label="Judge model" value={configData.judge_model} mono />
            <Metric
              label="Dataset v1"
              value={`${datasetData?.case_count ?? "—"} cases`}
            />
            <Metric
              label="Runs persisted"
              value={String(runsData?.total ?? "—")}
            />
          </section>

          {readinessData.issues.length > 0 && (
            <section className="panel">
              <h2>Environment issues</h2>
              <ul className="notes-list">
                {readinessData.issues.map((issue) => (
                  <li key={issue}>{issue}</li>
                ))}
              </ul>
            </section>
          )}

          {draftData ? (
            <section className="panel">
              <h2>Calibration draft</h2>
              <div className="grid">
                <Metric
                  label="Scored rows"
                  value={`${draftData.overview.scored_rows}/${draftData.overview.total_rows}`}
                />
                <Metric
                  label="Validation state"
                  value={draftData.overview.validation_state}
                />
                <Metric
                  label="Calibration"
                  value={draftData.overview.calibration_state}
                />
                <Metric
                  label="Pending reviewer rows"
                  value={String(draftData.overview.pending_reviewer_rows)}
                />
              </div>
              <p>
                <Link to="/calibration">Open the calibration workspace →</Link>
              </p>
            </section>
          ) : (
            <EmptyState>
              No calibration draft workspace exists yet; run the calibration
              draft preparation scripts before expecting one.
            </EmptyState>
          )}

          <section className="panel">
            <h2>Recent runs</h2>
            {runsData && runsData.items.length === 0 ? (
              <EmptyState>
                No evaluation runs persisted yet. Create one from the Runs
                page once a provider key is configured.
              </EmptyState>
            ) : runsData ? (
              <table>
                <thead>
                  <tr>
                    <th>Run</th>
                    <th>When</th>
                    <th>Model</th>
                    <th>Cases</th>
                    <th>Accuracy</th>
                    <th>Gate</th>
                  </tr>
                </thead>
                <tbody>
                  {runsData.items.map((run) => (
                    <tr key={run.run_id}>
                      <td>
                        <Link to={`/runs/${run.run_id}`}>
                          <span className="mono">{run.run_id}</span>
                        </Link>
                      </td>
                      <td>{formatTimestamp(run.timestamp)}</td>
                      <td className="mono">{run.model}</td>
                      <td>{run.case_count}</td>
                      <td>{pct(run.category_accuracy)}</td>
                      <td>
                        <StatusBadge status={run.regression_status} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : null}
          </section>
        </>
      )}
    </div>
  );
}
