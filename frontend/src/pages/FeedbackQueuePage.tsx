import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "../api/endpoints";
import { QueryState } from "../components/QueryState";
import { Pager } from "../components/Pager";
import { Badge } from "../components/Badge";
import { RoleGate } from "../components/RoleGate";
import { PredictionImage } from "../components/PredictionImage";

const LIMIT = 25;

export function FeedbackQueuePage() {
  const [offset, setOffset] = useState(0);
  const queryClient = useQueryClient();

  const { data, isLoading, error } = useQuery({
    queryKey: ["feedback-queue", offset],
    queryFn: () => api.listFeedbackQueue(LIMIT, offset),
  });

  const mutation = useMutation({
    mutationFn: api.submitFeedback,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["feedback-queue"] }),
  });

  return (
    <div className="stack">
      <h1>Feedback Queue</h1>
      <p className="muted">Predictions no operator has reviewed yet.</p>
      <QueryState
        isLoading={isLoading}
        error={error}
        isEmpty={data?.items.length === 0}
        emptyLabel="Nothing awaiting review."
      >
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Predicted at</th>
                <th>Image</th>
                <th>Verdict</th>
                <th>Score</th>
                <th>Confidence</th>
                <RoleGate minimum="operator">
                  <th>Review</th>
                </RoleGate>
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
                      label={prediction.is_anomalous ? "flagged defect" : "flagged good"}
                      tone={prediction.is_anomalous ? "bad" : "good"}
                    />
                  </td>
                  <td>{prediction.anomaly_score.toFixed(3)}</td>
                  <td>{(prediction.confidence * 100).toFixed(0)}%</td>
                  <RoleGate minimum="operator">
                    <td>
                      <div className="row">
                        <button
                          disabled={mutation.isPending}
                          onClick={() =>
                            mutation.mutate({
                              prediction_id: prediction.prediction_id,
                              verdict: "correct",
                            })
                          }
                        >
                          Confirm
                        </button>
                        <button
                          disabled={mutation.isPending}
                          onClick={() =>
                            mutation.mutate({
                              prediction_id: prediction.prediction_id,
                              verdict: "incorrect",
                              corrected_label: prediction.is_anomalous ? "good" : "defect",
                            })
                          }
                        >
                          Correct
                        </button>
                      </div>
                    </td>
                  </RoleGate>
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
