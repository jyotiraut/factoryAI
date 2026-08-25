import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import * as api from "../api/endpoints";
import { QueryState } from "../components/QueryState";
import { Pager } from "../components/Pager";
import { Badge } from "../components/Badge";
import { PredictionImage } from "../components/PredictionImage";

const LIMIT = 25;

export function PredictionHistoryPage() {
  const [offset, setOffset] = useState(0);

  const { data, isLoading, error } = useQuery({
    queryKey: ["predictions", offset],
    queryFn: () => api.listPredictions(LIMIT, offset),
  });

  return (
    <div className="stack">
      <h1>Prediction History</h1>
      <QueryState isLoading={isLoading} error={error} isEmpty={data?.items.length === 0}>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Predicted at</th>
                <th>Image</th>
                <th>Verdict</th>
                <th>Score</th>
                <th>Confidence</th>
                <th>Latency</th>
              </tr>
            </thead>
            <tbody>
              {data?.items.map((prediction) => (
                <tr key={prediction.prediction_id}>
                  <td>{new Date(prediction.predicted_at).toLocaleString()}</td>
                  <td>
                    <PredictionImage
                      imageUrl={prediction.image_url}
                      heatmapUrl={prediction.heatmap_url}
                    />
                  </td>
                  <td>
                    <Badge
                      label={prediction.is_anomalous ? "defect" : "good"}
                      tone={prediction.is_anomalous ? "bad" : "good"}
                    />
                  </td>
                  <td>
                    {prediction.anomaly_score.toFixed(3)} / {prediction.threshold.toFixed(3)}
                  </td>
                  <td>{(prediction.confidence * 100).toFixed(0)}%</td>
                  <td>{prediction.inference_time_ms.toFixed(1)} ms</td>
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
