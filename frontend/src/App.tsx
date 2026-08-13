import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Navigate, Route, BrowserRouter, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import { Layout } from "./components/Layout";
import { LoginPage } from "./pages/LoginPage";
import { LiveInspectionPage } from "./pages/LiveInspectionPage";
import { PredictionHistoryPage } from "./pages/PredictionHistoryPage";
import { FeedbackQueuePage } from "./pages/FeedbackQueuePage";
import { DefectTrendsPage } from "./pages/DefectTrendsPage";
import { ModelsPage } from "./pages/ModelsPage";
import { DeploymentsPage } from "./pages/DeploymentsPage";
import { DatasetVersionsPage } from "./pages/DatasetVersionsPage";
import { TrainingRunsPage } from "./pages/TrainingRunsPage";
import { DriftStatusPage } from "./pages/DriftStatusPage";
import { SystemHealthPage } from "./pages/SystemHealthPage";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10_000,
      retry: 1,
    },
  },
});

function RequireAuth({ children }: { children: ReactNode }) {
  const { user, isLoading } = useAuth();
  if (isLoading) return <p className="muted">Loading…</p>;
  if (!user) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        element={
          <RequireAuth>
            <Layout />
          </RequireAuth>
        }
      >
        <Route path="/" element={<Navigate to="/inspection" replace />} />
        <Route path="/inspection" element={<LiveInspectionPage />} />
        <Route path="/predictions" element={<PredictionHistoryPage />} />
        <Route path="/feedback-queue" element={<FeedbackQueuePage />} />
        <Route path="/defect-trends" element={<DefectTrendsPage />} />
        <Route path="/models" element={<ModelsPage />} />
        <Route path="/deployments" element={<DeploymentsPage />} />
        <Route path="/datasets" element={<DatasetVersionsPage />} />
        <Route path="/training-runs" element={<TrainingRunsPage />} />
        <Route path="/drift" element={<DriftStatusPage />} />
        <Route path="/system-health" element={<SystemHealthPage />} />
      </Route>
    </Routes>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
          <AppRoutes />
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
