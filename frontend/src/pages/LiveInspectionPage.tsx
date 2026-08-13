import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "../api/endpoints";
import { QueryState } from "../components/QueryState";
import { RoleGate } from "../components/RoleGate";
import { Badge } from "../components/Badge";

/**
 * Deliberately not a live camera feed — see ADR-0016. "Live" here means the front of the
 * feedback queue: the most recent prediction no operator has judged yet, presented one at
 * a time so reviewing it is the *only* thing on screen, which is what makes "under three
 * interactions" (this phase's own exit criterion) achievable — a single click confirms or
 * corrects it, and the next unreviewed prediction replaces it immediately.
 */
export function LiveInspectionPage() {
  const queryClient = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ["feedback-queue", "next"],
    queryFn: () => api.listFeedbackQueue(1, 0),
    refetchInterval: 15_000,
  });

  const mutation = useMutation({
    mutationFn: api.submitFeedback,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["feedback-queue"] });
    },
  });

  const prediction = data?.items[0];

  return (
    <div className="stack" style={{ maxWidth: 640 }}>
      <h1>Live Inspection</h1>
      <QueryState isLoading={isLoading} error={error} isEmpty={!prediction} emptyLabel="Queue is empty — nothing awaiting review.">
        {prediction && (
          <div className="card stack">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <span className="muted">Image {prediction.image_id}</span>
              <Badge label={prediction.is_anomalous ? "flagged defective" : "flagged good"} tone={prediction.is_anomalous ? "bad" : "good"} />
            </div>
            <div className="grid cols-3">
              <Stat label="Anomaly score" value={prediction.anomaly_score.toFixed(3)} />
              <Stat label="Threshold" value={prediction.threshold.toFixed(3)} />
              <Stat label="Confidence" value={`${(prediction.confidence * 100).toFixed(0)}%`} />
            </div>
            <p className="muted">Predicted {new Date(prediction.predicted_at).toLocaleString()}</p>
            <RoleGate minimum="operator">
              <div className="row">
                <button
                  className="primary"
                  disabled={mutation.isPending}
                  onClick={() =>
                    mutation.mutate({ prediction_id: prediction.prediction_id, verdict: "correct" })
                  }
                >
                  Confirm correct
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
                  Mark incorrect
                </button>
              </div>
            </RoleGate>
            {mutation.isError && <p className="error-text">Could not submit feedback.</p>}
          </div>
        )}
      </QueryState>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="muted" style={{ fontSize: "0.8em" }}>
        {label}
      </div>
      <div style={{ fontSize: "1.4em", fontWeight: 600 }}>{value}</div>
    </div>
  );
}
