import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import * as api from "../api/endpoints";
import { QueryState } from "../components/QueryState";
import { StageBadge } from "../components/Badge";

export function ModelsPage() {
  const summaries = useQuery({ queryKey: ["models"], queryFn: api.listModels });
  const [category, setCategory] = useState<string | null>(null);

  const activeCategory = category ?? summaries.data?.[0]?.category ?? null;

  const versions = useQuery({
    queryKey: ["model-versions", activeCategory],
    queryFn: () => api.listModelVersions(activeCategory!),
    enabled: !!activeCategory,
  });

  return (
    <div className="stack">
      <h1>Models</h1>
      <QueryState isLoading={summaries.isLoading} error={summaries.error} isEmpty={summaries.data?.length === 0}>
        <div className="grid cols-3">
          {summaries.data?.map((summary) => (
            <button
              key={summary.category}
              onClick={() => setCategory(summary.category)}
              style={{
                textAlign: "left",
                borderColor: activeCategory === summary.category ? "var(--accent)" : undefined,
              }}
            >
              <div className="stack">
                <strong>{summary.category}</strong>
                {summary.model_version_id ? (
                  <span className="muted">
                    v{summary.registry_version} · threshold {summary.threshold?.toFixed(3)}
                  </span>
                ) : (
                  <span className="muted">no production model</span>
                )}
              </div>
            </button>
          ))}
        </div>
      </QueryState>

      {activeCategory && (
        <div className="stack">
          <h2>{activeCategory} — registered versions</h2>
          <QueryState
            isLoading={versions.isLoading}
            error={versions.error}
            isEmpty={versions.data?.length === 0}
          >
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Registered</th>
                    <th>Registry</th>
                    <th>Stage</th>
                    <th>Threshold</th>
                  </tr>
                </thead>
                <tbody>
                  {versions.data?.map((version) => (
                    <tr key={version.model_version_id}>
                      <td>{new Date(version.created_at).toLocaleString()}</td>
                      <td className="muted">
                        {version.registry_name} v{version.registry_version}
                      </td>
                      <td>
                        <StageBadge stage={version.stage} />
                      </td>
                      <td>{version.threshold.toFixed(3)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </QueryState>
        </div>
      )}
    </div>
  );
}
