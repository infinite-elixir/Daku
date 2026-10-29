import { useQuery } from "@tanstack/react-query";

export type Health = {
  status: string;
  version: string;
  uptime_s: number;
  data_dir: string;
};

async function fetchHealth(): Promise<Health> {
  const response = await fetch("/api/health");
  if (!response.ok) {
    throw new Error(`health request failed: ${response.status}`);
  }
  return (await response.json()) as Health;
}

function formatUptime(seconds: number): string {
  if (seconds < 60) {
    return `${Math.round(seconds)}s`;
  }
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) {
    return `${minutes}m ${Math.round(seconds % 60)}s`;
  }
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}

export function HealthCard() {
  const { data, error, isLoading } = useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
    refetchInterval: 30_000,
  });

  return (
    <section className="rounded-panel border border-border-subtle bg-panel p-panel">
      <div className="flex items-center justify-between gap-4">
        <h2 className="text-h2 font-semibold tracking-tight text-fg">System health</h2>
        <span
          className={
            error
              ? "rounded-full bg-danger/15 px-3 py-1 text-micro font-medium text-danger"
              : isLoading
                ? "rounded-full bg-warning/15 px-3 py-1 text-micro font-medium text-warning"
                : "rounded-full bg-success/15 px-3 py-1 text-micro font-medium text-success"
          }
        >
          {error ? "unreachable" : isLoading ? "checking" : (data?.status ?? "unknown")}
        </span>
      </div>

      {error ? (
        <p className="mt-3 text-small text-danger">{(error as Error).message}</p>
      ) : isLoading || !data ? (
        <p className="mt-3 text-small text-fg-muted">Contacting the Daku service…</p>
      ) : (
        <dl className="mt-4 grid gap-3 text-small sm:grid-cols-3">
          <div>
            <dt className="text-fg-muted">Version</dt>
            <dd className="font-mono text-fg">{data.version}</dd>
          </div>
          <div>
            <dt className="text-fg-muted">Uptime</dt>
            <dd className="font-mono text-fg">{formatUptime(data.uptime_s)}</dd>
          </div>
          <div className="sm:col-span-1">
            <dt className="text-fg-muted">Data directory</dt>
            <dd className="break-all font-mono text-small text-fg">{data.data_dir}</dd>
          </div>
        </dl>
      )}
    </section>
  );
}
