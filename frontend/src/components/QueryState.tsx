import type { ReactNode } from "react";
import { ApiError } from "../api/client";

/**
 * The one place a loading/error/empty triad renders, so every view handles the same three
 * states the same way instead of re-inventing "still loading" copy per page.
 */
export function QueryState({
  isLoading,
  error,
  isEmpty,
  emptyLabel = "Nothing here yet.",
  children,
}: {
  isLoading: boolean;
  error: unknown;
  isEmpty?: boolean;
  emptyLabel?: string;
  children: ReactNode;
}) {
  if (isLoading) return <p className="muted">Loading…</p>;
  if (error) {
    const message = error instanceof ApiError ? error.message : "Something went wrong.";
    return <p className="error-text">{message}</p>;
  }
  if (isEmpty) return <p className="muted">{emptyLabel}</p>;
  return <>{children}</>;
}
