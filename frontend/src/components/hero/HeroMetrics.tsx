/**
 * Hero Metrics - Key statistics displayed in the hero section
 * Shows live discovery metrics like total opportunities, sources, etc.
 * 
 * Part of the Hero Component Set (Set 04)
 */
import { useMeta } from "../../hooks";
import { api } from "../../services/api";

export function HeroMetrics() {
  const stats = useMeta(() => api.stats());
  const status = useMeta(() => api.status());

  return (
    <div className="hero-metrics">
      <div className="metric-item">
        <span className="metric-label">Total Opportunities</span>
        <span className="metric-value">
          {stats.data?.total_opportunities ?? 0}
        </span>
      </div>
      <div className="metric-item">
        <span className="metric-label">Active Sources</span>
        <span className="metric-value">
          {stats.data?.sources_monitored ?? 0}
        </span>
      </div>
      <div className="metric-item">
        <span className="metric-label">Discovery Engine</span>
        <span className="metric-value">
          {status.data?.engine === "operational" ? "Operational" : 
           status.data?.engine === "degraded" ? "Degraded" : 
           status.data?.engine === "idle" ? "Standby" : "Unknown"}
        </span>
      </div>
      <div className="metric-item">
        <span className="metric-label">Last Updated</span>
        <span className="metric-value">
          {status.data?.last_discovery_label || "Just now"}
        </span>
      </div>
    </div>
  );
}
