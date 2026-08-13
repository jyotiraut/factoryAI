import { useQuery } from "@tanstack/react-query";
import * as api from "../api/endpoints";
import { QueryState } from "../components/QueryState";
import { StatusBadge } from "../components/Badge";

export function SystemHealthPage() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["system-health"],
    queryFn: api.getSystemHealth,
    refetchInterval: 15_000,
  });

  return (
    <div className="stack">
      <h1>System Health</h1>
      <QueryState isLoading={isLoading} error={error}>
        {data && (
          <div className="stack">
            <div className="grid cols-4">
              <Gauge label="CPU" value={data.cpu_percent} />
              <Gauge label="Memory" value={data.memory_percent} />
              <Gauge label="Disk" value={data.disk_percent} />
              <Gauge label="Model cache hit ratio" value={data.model_cache_hit_ratio * 100} />
            </div>
            <div className="card stack">
              <h2>Jobs by status</h2>
              <div className="row">
                {Object.entries(data.jobs_by_status).map(([status, count]) => (
                  <div key={status} className="row">
                    <StatusBadge status={status} />
                    <span>{count}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </QueryState>
    </div>
  );
}

function Gauge({ label, value }: { label: string; value: number }) {
  const tone = value > 90 ? "var(--bad)" : value > 75 ? "var(--warn)" : "var(--good)";
  return (
    <div className="card stack">
      <span className="muted">{label}</span>
      <span style={{ fontSize: "1.8em", fontWeight: 700, color: tone }}>{value.toFixed(1)}%</span>
    </div>
  );
}
