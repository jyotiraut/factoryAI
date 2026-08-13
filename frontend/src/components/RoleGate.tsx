import type { ReactNode } from "react";
import { roleAtLeast, useAuth } from "../auth/AuthContext";
import type { CurrentUser } from "../api/types";

/** Hides its children entirely rather than disabling them — the backend still enforces
 * every permission independently (Phase 8's RBAC matrix), so this is a UI convenience,
 * never the actual access boundary. */
export function RoleGate({
  minimum,
  children,
}: {
  minimum: CurrentUser["role"];
  children: ReactNode;
}) {
  const { user } = useAuth();
  if (!roleAtLeast(user, minimum)) return null;
  return <>{children}</>;
}
