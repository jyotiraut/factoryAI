import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import * as api from "../api/endpoints";
import { QueryState } from "../components/QueryState";
import { Pager } from "../components/Pager";
import { Badge, SeverityBadge } from "../components/Badge";

const LIMIT = 25;

export function DriftStatusPage() {
  const [offset, setOffset] = useState(0);
  const { data, isLoading, error } = useQuery({
    queryKey: ["drift-reports", offset],
    queryFn: () => api.listDriftReports(LIMIT, offset),
  });

  return (
    <div className="stack">
      <h1>Drift Status</h1>
      <QueryState isLoading={isLoading} error={error} isEmpty={data?.items.length === 0}>
        <div className="stack">
          {data?.items.map((report) => (
            <div key={report.report_id} className="card stack">
              <div className="row" style={{ justifyContent: "space-between" }}>
                <div className="row">
                  <SeverityBadge severity={report.severity} />
                  {report.should_trigger_retraining && (
                    <Badge label="retraining triggered" tone="warn" />
                  )}
                </div>
                <span className="muted">{new Date(report.created_at).toLocaleString()}</span>
              </div>
              <p className="muted">
                Window {new Date(report.window_start).toLocaleDateString()} –{" "}
                {new Date(report.window_end).toLocaleDateString()} · {report.sample_count} samples
              </p>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Signal</th>
                      <th>Statistic</th>
                      <th>Threshold</th>
                      <th>Method</th>
                      <th>Breached</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.signals.map((signal) => (
                      <tr key={signal.name}>
                        <td>{signal.name}</td>
                        <td>{signal.statistic.toFixed(4)}</td>
                        <td className="muted">{signal.threshold.toFixed(4)}</td>
                        <td className="muted">{signal.method}</td>
                        <td>
                          <Badge label={signal.breached ? "yes" : "no"} tone={signal.breached ? "bad" : "good"} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ))}
        </div>
      </QueryState>
      {data && <Pager total={data.total} limit={LIMIT} offset={offset} onOffsetChange={setOffset} />}
    </div>
  );
}
