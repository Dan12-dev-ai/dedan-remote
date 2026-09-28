/**
 * Hero Status - Indicates the current state of the discovery engine
 * Shows operational status, health metrics, or system information
 * 
 * Part of the Hero Component Set (Set 04)
 */
import { useMeta } from "../../hooks";
import { api } from "../../services/api";

export function HeroStatus() {
  const status = useMeta(() => api.status());

  return (
    <div className="hero-status">
      <div className="status-item">
        <span className="status-label">Engine</span>
        <span className="status-value">
          {status.data?.engine === "operational" ? "Operational" : 
           status.data?.engine === "degraded" ? "Degraded" : 
           status.data?.engine === "idle" ? "Standby" : "Unknown"}
        </span>
      </div>
      <div className="status-item">
        <span className="status-label">Last Discovery</span>
        <span className="status-value">
          {status.data?.last_discovery_label || "Just now"}
        </span>
      </div>
    </div>
  );
}
