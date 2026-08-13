import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

const NAV_ITEMS = [
  { to: "/inspection", label: "Live Inspection" },
  { to: "/predictions", label: "Prediction History" },
  { to: "/feedback-queue", label: "Feedback Queue" },
  { to: "/defect-trends", label: "Defect Trends" },
  { to: "/models", label: "Models" },
  { to: "/deployments", label: "Deployments" },
  { to: "/datasets", label: "Dataset Versions" },
  { to: "/training-runs", label: "Training Runs" },
  { to: "/drift", label: "Drift Status" },
  { to: "/system-health", label: "System Health" },
];

export function Layout() {
  const { user, logout } = useAuth();

  return (
    <div style={{ display: "flex", minHeight: "100vh" }}>
      <nav
        style={{
          width: 220,
          borderRight: "1px solid var(--border)",
          background: "var(--bg-panel)",
          padding: "1.25em 0.75em",
          flexShrink: 0,
        }}
      >
        <div style={{ padding: "0 0.5em", marginBottom: "1.5em" }}>
          <strong style={{ color: "var(--accent)", fontSize: "1.1em" }}>FactoryAI</strong>
        </div>
        <div className="stack">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              style={({ isActive }) => ({
                padding: "0.5em 0.75em",
                borderRadius: 6,
                color: isActive ? "var(--text)" : "var(--text-dim)",
                background: isActive ? "var(--bg-panel-raised)" : "transparent",
                textDecoration: "none",
              })}
            >
              {item.label}
            </NavLink>
          ))}
        </div>
      </nav>
      <div style={{ flex: 1, display: "flex", flexDirection: "column" }}>
        <header
          className="row"
          style={{
            justifyContent: "flex-end",
            padding: "0.75em 1.5em",
            borderBottom: "1px solid var(--border)",
          }}
        >
          <span className="muted">
            {user?.email} · <span style={{ color: "var(--accent)" }}>{user?.role}</span>
          </span>
          <button onClick={logout}>Log out</button>
        </header>
        <main style={{ flex: 1, padding: "1.5em", overflowX: "auto" }}>
          <Outlet />
        </main>
      </div>
    </div>
  );
}
