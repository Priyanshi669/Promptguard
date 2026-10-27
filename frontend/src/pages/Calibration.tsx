import { useCallback, useEffect, useState } from "react";
import {
  calibrationOverview,
  calibrationRows,
} from "../api/client";
import type {
  CalibrationRowsResponse,
  DraftOverviewResponse,
  DraftRow,
} from "../api/types";
import {
  EmptyState,
  ErrorBanner,
  Loading,
  Metric,
} from "../components/ui";

const PAGE_SIZE = 25;

export function CalibrationPage() {
  const [overview, setOverview] = useState<DraftOverviewResponse | null>(null);
  const [rows, setRows] = useState<CalibrationRowsResponse | null>(null);
  const [page, setPage] = useState(0);
  const [showValidation, setShowValidation] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [rowsError, setRowsError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setOverview(await calibrationOverview());
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const loadRows = useCallback(async (offset: number) => {
    setRowsError(null);
    try {
      setRows(await calibrationRows(offset, PAGE_SIZE));
    } catch (err) {
      setRowsError(err);
    }
  }, []);

  useEffect(() => {
    void loadRows(page * PAGE_SIZE);
  }, [loadRows, page]);

  if (loading) {
    return (
      <div>
        <h1>Calibration</h1>
        <Loading />
      </div>
    );
  }
  if (error !== null) {
    return (
      <div>
        <h1>Calibration</h1>
        <ErrorBanner error={error} />
      </div>
    );
  }
  if (!overview) {
    return (
      <div>
        <h1>Calibration</h1>
        <EmptyState>No calibration data available.</EmptyState>
      </div>
    );
  }

  const o = overview.overview;
  const strict = o.strict_validation;

  return (
    <div>
      <h1>Calibration workspace</h1>
      <p className="notes-list" style={{ marginBottom: "1rem" }}>
        {overview.note}
      </p>

      <section className="grid">
        <Metric label="Draft state" value={o.draft_state} />
        <Metric
          label="Scored rows"
          value={`${o.scored_rows}/${o.total_rows}`}
        />
        <Metric
          label="Pending reviewer rows"
          value={String(o.pending_reviewer_rows)}
        />
        <Metric label="Validation state" value={o.validation_state} />
        <Metric label="Calibration" value={o.calibration_state} />
        <Metric
          label="Double-annotated cases"
          value={String(o.double_annotation_cases)}
        />
      </section>

      <section className="panel">
        <h2>Annotator progress</h2>
        <table>
          <tbody>
            <tr>
              <td>Primary rows</td>
              <td>{o.primary_rows}</td>
            </tr>
            <tr>
              <td>Secondary rows (independent pass)</td>
              <td>{o.secondary_rows}</td>
            </tr>
            <tr>
              <td>Scored unique cases</td>
              <td>{o.scored_unique_cases}</td>
            </tr>
            <tr>
              <td>Candidate models</td>
              <td className="mono">{o.candidate_models.join(", ")}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <section className="panel">
        <h2>Strict validation (read-only)</h2>
        {strict ? (
          <>
            <div className="grid">
              <Metric label="Validator status" value={strict.status} />
              <Metric
                label="Pending reviewer fields"
                value={String(strict.pending_reviewer_field_count)}
              />
              <Metric label="Defects" value={String(strict.defect_count)} />
              <Metric label="Warnings" value={String(strict.warning_count)} />
            </div>
            {strict.defect_count > 0 && (
              <table>
                <thead>
                  <tr>
                    <th>Row</th>
                    <th>Field</th>
                    <th>Finding</th>
                    <th>Hint</th>
                  </tr>
                </thead>
                <tbody>
                  {strict.defects.map((defect, index) => (
                    <tr key={index}>
                      <td className="mono">{defect.row_id}</td>
                      <td>{defect.field}</td>
                      <td>{defect.message}</td>
                      <td>{defect.hint ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <button
              type="button"
              onClick={() => setShowValidation((current) => !current)}
            >
              {showValidation ? "Hide raw errors" : "Show raw errors"}
            </button>
            {showValidation && (
              <ul className="notes-list">
                {strict.raw_errors.map((rawError, index) => (
                  <li key={index}>
                    <span className="mono">{rawError}</span>
                  </li>
                ))}
              </ul>
            )}
          </>
        ) : (
          <EmptyState>
            Strict validation has not run in this view
            {o.validation_state === "not_run" ? " (state: not_run)." : "."}
          </EmptyState>
        )}
      </section>

      {o.provenance_notes.length > 0 && (
        <section className="panel">
          <h2>Provenance notes</h2>
          <ul className="notes-list">
            {o.provenance_notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </section>
      )}

      <section className="panel">
        <h2>
          Draft rows
          {rows ? ` (${rows.offset + 1}–${rows.offset + rows.items.length} of ${rows.total})` : ""}
        </h2>
        <ErrorBanner error={rowsError} />
        {rows && rows.items.length === 0 ? (
          <EmptyState>No draft rows found.</EmptyState>
        ) : rows ? (
          <>
            <table>
              <thead>
                <tr>
                  <th>Case</th>
                  <th>Pass</th>
                  <th>Category</th>
                  <th>Candidate summary</th>
                  <th>Reference</th>
                  <th>Score</th>
                  <th>Confidence</th>
                  <th>Annotator</th>
                </tr>
              </thead>
              <tbody>
                {rows.items.map((row: DraftRow) => (
                  <tr key={`${row.id}/${row.annotation_pass}`}>
                    <td className="mono">{row.id}</td>
                    <td>{row.annotation_pass}</td>
                    <td className="mono">{row.category}</td>
                    <td>
                      {trim(row.actual_summary, 90)}
                      {row.actual_summary_is_ai_candidate && " (AI)"}
                    </td>
                    <td>
                      {row.expected_summary
                        ? trim(row.expected_summary, 90)
                        : "— blank —"}
                    </td>
                    <td>{row.human_score || "—"}</td>
                    <td>{row.confidence || "—"}</td>
                    <td>{row.annotator_id || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p>
              <button
                type="button"
                disabled={page === 0}
                onClick={() => setPage((p) => Math.max(0, p - 1))}
              >
                ← Previous
              </button>{" "}
              <button
                type="button"
                disabled={(page + 1) * PAGE_SIZE >= rows.total}
                onClick={() => setPage((p) => p + 1)}
              >
                Next →
              </button>{" "}
              <span className="notes-list">Page {page + 1}</span>
            </p>
          </>
        ) : null}
      </section>
    </div>
  );
}

function trim(text: string, max: number): string {
  const single = text.replace(/\s+/g, " ").trim();
  return single.length > max ? `${single.slice(0, max - 1)}…` : single;
}
