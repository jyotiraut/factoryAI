import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "../api/endpoints";
import { QueryState } from "../components/QueryState";
import { RoleGate } from "../components/RoleGate";
import { Badge } from "../components/Badge";
import type { PredictionResponse } from "../api/types";

/**
 * Deliberately not a live camera feed — see ADR-0016. "Live" here means the front of the
 * feedback queue: the most recent prediction no operator has judged yet, presented one at
 * a time so reviewing it is the *only* thing on screen, which is what makes "under three
 * interactions" (this phase's own exit criterion) achievable — a single click confirms or
 * corrects it, and the next unreviewed prediction replaces it immediately.
 *
 * The upload form below is the one gap that left: ADR-0016 shipped this page as a pure
 * review queue with nothing to submit a new image through, so testing the model at all
 * meant going around the frontend entirely (curl, `/docs`). Submitting still requires
 * `submit_prediction` (operator+), matching the backend's own gate on `POST /predict`.
 */
export function LiveInspectionPage() {
  const queryClient = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ["feedback-queue", "next"],
    queryFn: () => api.listFeedbackQueue(1, 0),
    refetchInterval: 15_000,
  });

  const models = useQuery({ queryKey: ["models"], queryFn: api.listModels });

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
      <RoleGate minimum="operator">
        <UploadCard
          categories={models.data?.map((m) => m.category) ?? []}
          onSubmitted={() => queryClient.invalidateQueries({ queryKey: ["feedback-queue"] })}
        />
      </RoleGate>
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

function UploadCard({
  categories,
  onSubmitted,
}: {
  categories: string[];
  onSubmitted: () => void;
}) {
  const [category, setCategory] = useState<string>("");
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<PredictionResponse | null>(null);

  const activeCategory = category || categories[0] || "";

  const mutation = useMutation({
    mutationFn: () => api.predictImage(activeCategory, file as File),
    onSuccess: (response) => {
      setResult(response);
      onSubmitted();
    },
  });

  return (
    <div className="card stack">
      <h2 style={{ margin: 0, fontSize: "1em" }}>Submit an image for inspection</h2>
      <div className="row" style={{ gap: "0.5rem", alignItems: "center" }}>
        <select value={activeCategory} onChange={(e) => setCategory(e.target.value)}>
          {categories.length === 0 && <option value="">No categories available</option>}
          {categories.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        <input
          type="file"
          accept="image/png,image/jpeg,image/bmp,image/tiff"
          onChange={(e) => {
            setFile(e.target.files?.[0] ?? null);
            setResult(null);
          }}
        />
        <button
          className="primary"
          disabled={!file || !activeCategory || mutation.isPending}
          onClick={() => mutation.mutate()}
        >
          {mutation.isPending ? "Scoring…" : "Submit"}
        </button>
      </div>
      {mutation.isError && <p className="error-text">Could not score that image.</p>}
      {result && (
        <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
          <Badge label={result.is_anomalous ? "flagged defective" : "flagged good"} tone={result.is_anomalous ? "bad" : "good"} />
          <span className="muted">
            score {result.anomaly_score.toFixed(3)} / threshold {result.threshold.toFixed(3)}
          </span>
        </div>
      )}
    </div>
  );
}
