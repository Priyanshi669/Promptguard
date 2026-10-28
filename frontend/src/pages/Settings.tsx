import { useCallback, useEffect, useState } from "react";
import { config } from "../api/client";
import type { ConfigResponse } from "../api/types";
import { ErrorBanner, Loading, Panel } from "../components/ui";

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="py-space-xs flex items-center justify-between font-code-sm text-code-sm">
      <span className="text-on-surface">{label}</span>
      <span className="text-on-surface-variant font-mono text-right">
        {value}
      </span>
    </div>
  );
}

export function SettingsPage() {
  const [data, setData] = useState<ConfigResponse | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await config());
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
    <div className="flex flex-col w-full">
      <div className="w-full bg-surface-container-lowest px-gutter py-space-sm flex flex-wrap items-center justify-between gap-space-md">
        <span className="font-code-sm text-code-sm text-on-surface font-medium">
          Settings
        </span>
        <span className="font-code-sm text-code-sm text-outline">
          Read-only server configuration
        </span>
      </div>
      <div className="w-full p-gutter flex flex-col gap-space-md">
        <ErrorBanner error={error} />
        {loading && <Loading />}
        {data && (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-space-md">
            <Panel title="Models">
              <Row label="Candidate model" value={data.candidate_model} />
              <Row label="Candidate base URL" value={data.candidate_base_url} />
              <Row label="Judge model" value={data.judge_model} />
              <Row label="Judge base URL" value={data.judge_base_url} />
            </Panel>
            <Panel title="Limits & timeouts">
              <Row
                label="Request timeout"
                value={`${data.request_timeout_seconds}s`}
              />
              <Row
                label="Min request interval"
                value={`${data.min_request_interval_seconds}s`}
              />
              <Row
                label="Evaluation timeout"
                value={`${data.evaluation_timeout_seconds}s`}
              />
            </Panel>
            <Panel title="Bootstrap statistics">
              <Row label="Seed" value={String(data.bootstrap.seed)} />
              <Row label="Resamples" value={String(data.bootstrap.resamples)} />
              <Row
                label="Confidence level"
                value={String(data.bootstrap.confidence_level)}
              />
            </Panel>
            <Panel title="Notifications & auth">
              <Row label="Slack notifications" value={data.slack_notifications} />
              <Row
                label="Credentials in identities"
                value={String(data.credentials_in_identities)}
              />
              <Row
                label="Prompt versions"
                value={data.prompt_versions.join(", ") || "—"}
              />
            </Panel>
            <Panel title="Datasets">
              {Object.entries(data.datasets).map(([name, count]) => (
                <Row key={name} label={name} value={`${count} cases`} />
              ))}
              {Object.keys(data.datasets).length === 0 && (
                <span className="empty">No datasets configured.</span>
              )}
            </Panel>
            <Panel title="Provider pool">
              {data.provider_pool.map((entry) => (
                <Row key={entry.name} label={entry.name} value={entry.model} />
              ))}
              {data.provider_pool.length === 0 && (
                <span className="empty">No providers configured.</span>
              )}
            </Panel>
          </div>
        )}
      </div>
    </div>
  );
}
