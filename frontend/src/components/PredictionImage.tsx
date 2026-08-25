import { useState } from "react";

/**
 * A small thumbnail that expands to the full inspected image on click, with an optional
 * heatmap-overlay toggle. Every dashboard view that lists predictions used to show only a
 * truncated `image_id` string here — a real, disclosed gap (ADR-0016): a visual-inspection
 * tool that never let a reviewer see the actual picture. `image_url`/`heatmap_url` are
 * presigned server-side per request (`GET /predictions`, `.../feedback-queue`), so no
 * client-side URL construction happens here.
 */
export function PredictionImage({
  imageUrl,
  heatmapUrl,
  alt = "Inspected product",
}: {
  imageUrl?: string | null;
  heatmapUrl?: string | null;
  alt?: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const [showHeatmap, setShowHeatmap] = useState(false);

  if (!imageUrl) {
    return <span className="muted">no image</span>;
  }

  if (!expanded) {
    return (
      <img
        src={imageUrl}
        alt={alt}
        onClick={() => setExpanded(true)}
        style={{
          width: 40,
          height: 40,
          objectFit: "cover",
          borderRadius: 4,
          cursor: "zoom-in",
        }}
      />
    );
  }

  return (
    <div className="stack" style={{ gap: "0.4rem" }}>
      <div
        onClick={() => setExpanded(false)}
        style={{ position: "relative", width: 280, cursor: "zoom-out" }}
      >
        <img src={imageUrl} alt={alt} style={{ width: "100%", display: "block", borderRadius: 4 }} />
        {heatmapUrl && showHeatmap && (
          <img
            src={heatmapUrl}
            alt="Anomaly heatmap"
            style={{
              position: "absolute",
              inset: 0,
              width: "100%",
              height: "100%",
              opacity: 0.65,
              mixBlendMode: "screen",
            }}
          />
        )}
      </div>
      {heatmapUrl && (
        <label className="row" style={{ gap: "0.4rem", alignItems: "center", fontSize: "0.85em" }}>
          <input
            type="checkbox"
            checked={showHeatmap}
            onChange={(e) => setShowHeatmap(e.target.checked)}
          />
          Show anomaly heatmap
        </label>
      )}
    </div>
  );
}
