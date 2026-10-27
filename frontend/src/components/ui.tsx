import type { ApiError } from "../api/client";
import type { ReactNode } from "react";

export function StatusBadge({ status }: { status: string | null }) {
  if (!status) {
    return <span className="badge badge-neutral">—</span>;
  }
  return <span className={`badge badge-${status}`}>{status}</span>;
}

export function Loading({ children }: { children?: ReactNode }) {
  return <div className="loading">{children ?? "Loading…"}</div>;
}

export function ErrorBanner(
  { error }: { error: unknown },
) {
  if (error === null || error === undefined) {
    return null;
  }
  if (
    typeof error === "object" &&
    error !== null &&
    "status" in error &&
    "code" in error
  ) {
    const apiError = error as ApiError;
    return (
      <div className="error-banner" role="alert">
        <span className="code">{apiError.code}</span>
        {apiError.message}
      </div>
    );
  }
  const message =
    error instanceof Error ? error.message : String(error);
  return (
    <div className="error-banner" role="alert">
      <span className="code">transport</span>
      {message}
    </div>
  );
}

export function Metric(
  { label, value, mono }: { label: string; value: string; mono?: boolean },
) {
  return (
    <div className="metric">
      <div className="label">{label}</div>
      <div className={`value${mono ? " mono" : ""}`}>{value}</div>
    </div>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function pct(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

export function formatTimestamp(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) {
    return iso;
  }
  return parsed.toLocaleString();
}
