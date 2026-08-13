import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import * as api from "../api/endpoints";
import { QueryState } from "../components/QueryState";
import { Pager } from "../components/Pager";
import { StatusBadge } from "../components/Badge";

const LIMIT = 25;

export function TrainingRunsPage() {
  const [offset, setOffset] = useState(0);
  const { data, isLoading, error } = useQuery({
    queryKey: ["training-runs", offset],
    queryFn: () => api.listTrainingRuns(LIMIT, offset),
  });

  return (
    <div className="stack">
      <h1>Training Runs</h1>
      <QueryState isLoading={isLoading} error={error} isEmpty={data?.items.length === 0}>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Started</th>
                <th>Model family</th>
                <th>Backbone</th>
                <th>Status</th>
                <th>Image AUROC</th>
                <th>Failure</th>
              </tr>
            </thead>
            <tbody>
              {data?.items.map((run) => (
                <tr key={run.experiment_id}>
                  <td>{new Date(run.started_at).toLocaleString()}</td>
                  <td>{run.model_family}</td>
                  <td className="muted">{run.backbone}</td>
                  <td>
                    <StatusBadge status={run.status} />
                  </td>
                  <td>{formatMetric(run.metrics?.image_auroc)}</td>
                  <td className="muted">{run.failure_reason || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </QueryState>
      {data && <Pager total={data.total} limit={LIMIT} offset={offset} onOffsetChange={setOffset} />}
    </div>
  );
}

function formatMetric(value: number | number[] | null | undefined): string {
  return typeof value === "number" ? value.toFixed(4) : "—";
}
