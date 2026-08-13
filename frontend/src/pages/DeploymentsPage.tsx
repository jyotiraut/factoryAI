import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import * as api from "../api/endpoints";
import { QueryState } from "../components/QueryState";
import { Badge } from "../components/Badge";

const ACTION_TONE = { promote: "good", rollback: "warn", reject: "bad" } as const;

export function DeploymentsPage() {
  const summaries = useQuery({ queryKey: ["models"], queryFn: api.listModels });
  const [category, setCategory] = useState<string | null>(null);
  const activeCategory = category ?? summaries.data?.[0]?.category ?? null;

  const deployments = useQuery({
    queryKey: ["deployments", activeCategory],
    queryFn: () => api.listDeployments(activeCategory!),
    enabled: !!activeCategory,
  });

  return (
    <div className="stack">
      <h1>Deployment History</h1>
      <label className="row">
        Category
        <select value={activeCategory ?? ""} onChange={(e) => setCategory(e.target.value)}>
          {summaries.data?.map((summary) => (
            <option key={summary.category} value={summary.category}>
              {summary.category}
            </option>
          ))}
        </select>
      </label>
      <QueryState
        isLoading={deployments.isLoading}
        error={deployments.error}
        isEmpty={deployments.data?.length === 0}
      >
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>When</th>
                <th>Action</th>
                <th>Environment</th>
                <th>Model version</th>
                <th>Reason</th>
              </tr>
            </thead>
            <tbody>
              {deployments.data?.map((deployment) => (
                <tr key={deployment.deployment_id}>
                  <td>{new Date(deployment.deployed_at).toLocaleString()}</td>
                  <td>
                    <Badge label={deployment.action} tone={ACTION_TONE[deployment.action]} />
                  </td>
                  <td>{deployment.environment}</td>
                  <td className="muted">{deployment.model_version_id.slice(0, 8)}</td>
                  <td className="muted">{deployment.reason || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </QueryState>
    </div>
  );
}
