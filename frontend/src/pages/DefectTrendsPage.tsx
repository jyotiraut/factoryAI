import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import * as api from "../api/endpoints";
import { QueryState } from "../components/QueryState";

export function DefectTrendsPage() {
  const summaries = useQuery({ queryKey: ["models"], queryFn: api.listModels });
  const [category, setCategory] = useState<string | null>(null);
  const activeCategory = category ?? summaries.data?.[0]?.category ?? null;

  const trend = useQuery({
    queryKey: ["defect-trend", activeCategory],
    queryFn: () => api.getDefectTrend(activeCategory!, 30),
    enabled: !!activeCategory,
  });

  const chartData = trend.data?.map((point) => ({
    ...point,
    ratePercent: Math.round(point.rate * 1000) / 10,
  }));

  return (
    <div className="stack">
      <h1>Defect Trends</h1>
      <label className="row">
        Category
        <select value={activeCategory ?? ""} onChange={(e) => setCategory(e.target.value)}>
          {summaries.data?.map((summary) => (
            <option key={summary.category} value={summary.category}>
              {summary.category}
            </option>
          ))}
        </select>
      </label>
      <QueryState
        isLoading={trend.isLoading}
        error={trend.error}
        isEmpty={chartData?.length === 0}
        emptyLabel="No production traffic in the last 30 days for this category."
      >
        <div className="card" style={{ height: 340 }}>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData}>
              <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
              <XAxis dataKey="day" stroke="var(--text-dim)" fontSize={12} />
              <YAxis
                stroke="var(--text-dim)"
                fontSize={12}
                unit="%"
                domain={[0, 100]}
                allowDataOverflow
              />
              <Tooltip
                contentStyle={{ background: "var(--bg-panel-raised)", border: "1px solid var(--border)" }}
                formatter={(value) => [`${value}%`, "Defect rate"]}
              />
              <Line
                type="monotone"
                dataKey="ratePercent"
                stroke="var(--accent)"
                strokeWidth={2}
                dot={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </QueryState>
    </div>
  );
}
