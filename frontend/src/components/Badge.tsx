type Tone = "good" | "warn" | "bad" | "neutral";

const SEVERITY_TONE: Record<string, Tone> = {
  none: "good",
  low: "good",
  medium: "warn",
  high: "bad",
};

const STAGE_TONE: Record<string, Tone> = {
  production: "good",
  staging: "warn",
  development: "neutral",
  archived: "neutral",
};

const STATUS_TONE: Record<string, Tone> = {
  succeeded: "good",
  completed: "good",
  running: "warn",
  queued: "neutral",
  failed: "bad",
};

export function Badge({ label, tone }: { label: string; tone: Tone }) {
  return <span className={`badge ${tone}`}>{label}</span>;
}

export function SeverityBadge({ severity }: { severity: string }) {
  return <Badge label={severity} tone={SEVERITY_TONE[severity] ?? "neutral"} />;
}

export function StageBadge({ stage }: { stage: string }) {
  return <Badge label={stage} tone={STAGE_TONE[stage] ?? "neutral"} />;
}

export function StatusBadge({ status }: { status: string }) {
  return <Badge label={status} tone={STATUS_TONE[status] ?? "neutral"} />;
}
