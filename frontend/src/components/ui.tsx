import type { ApiError } from "../api/client";
import type { ReactNode } from "react";

const BADGE_TONES: Record<string, string> = {
  PASS: "bg-secondary/10 text-secondary",
  FAIL: "bg-error/10 text-error",
  INCONCLUSIVE: "bg-tertiary/10 text-tertiary",
};

const DOT_TONES: Record<string, string> = {
  PASS: "bg-secondary",
  FAIL: "bg-error",
  INCONCLUSIVE: "bg-tertiary",
};

export function StatusBadge({ status }: { status: string | null }) {
  if (!status) {
    return <span className="text-outline font-code-sm text-code-sm">—</span>;
  }
  const tone =
    BADGE_TONES[status] ?? "bg-tertiary/10 text-tertiary";
  return (
    <span
      className={`inline-flex items-center px-1.5 py-0.5 rounded font-label-sm text-label-sm font-medium ${tone}`}
    >
      {status}
    </span>
  );
}

export function GateDot({ status }: { status: string | null }) {
  if (!status) {
    return null;
  }
  return (
    <span
      className={`w-1.5 h-1.5 rounded-full ${DOT_TONES[status] ?? "bg-tertiary"}`}
    />
  );
}

export function Loading({ children }: { children?: ReactNode }) {
  return <div className="loading">{children ?? "Loading…"}</div>;
}

export function ErrorBanner({ error }: { error: unknown }) {
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
      <div
        className="p-space-sm rounded bg-error-container/30 text-error font-body-md text-body-md border border-error/30"
        role="alert"
      >
        <span className="font-code-sm text-code-sm mr-space-sm" >
          {apiError.code}
        </span>
        {apiError.message}
      </div>
    );
  }
  const message =
    error instanceof Error ? error.message : String(error);
  return (
    <div className="p-space-sm rounded bg-error-container/30 text-error font-body-md text-body-md border border-error/30" role="alert">
      <span className="font-code-sm text-code-sm mr-space-sm">transport</span>
      {message}
    </div>
  );
}

export function Metric({
  label,
  value,
  mono,
  hint,
}: {
  label: string;
  value: string;
  mono?: boolean;
  hint?: string;
}) {
  return (
    <div className="bg-surface-container-low p-space-md rounded flex flex-col justify-between gap-space-xs">
      <div className="flex items-center justify-between text-outline">
        <span className="font-label-sm text-label-sm uppercase tracking-wider">
          {label}
        </span>
      </div>
      <div className="flex items-baseline gap-space-sm">
        <span
          className={`font-code-lg font-semibold text-on-surface leading-none ${mono ? "font-mono" : ""}`}
        >
          {value}
        </span>
        {hint && (
          <span className="font-code-sm text-code-sm text-outline">
            {hint}
          </span>
        )}
      </div>
    </div>
  );
}

export function Panel({
  title,
  actions,
  children,
}: {
  title?: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="bg-surface-container-lowest p-space-md rounded flex flex-col gap-space-sm">
      {(title || actions) && (
        <div className="flex items-center justify-between font-label-sm text-label-sm text-outline uppercase tracking-wider">
          {title && <span className="text-on-surface font-semibold">{title}</span>}
          {actions}
        </div>
      )}
      {children}
    </div>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return (
    <div className="font-body-md text-body-md text-outline p-space-md">
      {children}
    </div>
  );
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
