/**
 * Hero Eyebrow - Clean status pill for verified enterprise intelligence
 */
import { useMeta } from "../../hooks";
import { api } from "../../services/api";

export function HeroEyebrow() {
  const status = useMeta(() => api.status());

  const engineLabel =
    status.data?.engine === "operational"
      ? "Operational"
      : status.data?.engine === "degraded"
        ? "Degraded"
        : status.data?.engine === "idle"
          ? "Standby"
          : "Active";

  const engineClass =
    status.data?.engine === "operational"
      ? "ok"
      : status.data?.engine === "degraded"
        ? "warn"
        : "ok";

  return (
    <div className="hero-eyebrow">
      <span className={`hero-pulse hero-pulse--${engineClass}`} aria-hidden="true" />
      <span className="hero-eyebrow-text">
        Autonomous Global Discovery
        <span className="hero-eyebrow-separator">·</span>
        <span className="hero-eyebrow-status">{engineLabel}</span>
      </span>
      {status.data?.last_discovery_label && (
        <span className="hero-eyebrow-dim">
          · updated {status.data.last_discovery_label.toLowerCase()}
        </span>
      )}
    </div>
  );
}
