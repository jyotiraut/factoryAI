import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import * as api from "../api/endpoints";
import { QueryState } from "../components/QueryState";
import { Pager } from "../components/Pager";

const LIMIT = 25;

export function DatasetVersionsPage() {
  const [offset, setOffset] = useState(0);
  const { data, isLoading, error } = useQuery({
    queryKey: ["dataset-versions", offset],
    queryFn: () => api.listDatasetVersions(LIMIT, offset),
  });

  return (
    <div className="stack">
      <h1>Dataset Versions</h1>
      <QueryState isLoading={isLoading} error={error} isEmpty={data?.items.length === 0}>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Created</th>
                <th>Tag</th>
                <th>Images</th>
                <th>DVC hash</th>
                <th>Git commit</th>
                <th>Note</th>
              </tr>
            </thead>
            <tbody>
              {data?.items.map((version) => (
                <tr key={version.version_id}>
                  <td>{new Date(version.created_at).toLocaleString()}</td>
                  <td>{version.version_tag}</td>
                  <td>{version.image_count}</td>
                  <td className="muted">{version.dvc_hash.slice(0, 10)}</td>
                  <td className="muted">{version.git_commit.slice(0, 8)}</td>
                  <td className="muted">{version.note || "—"}</td>
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
