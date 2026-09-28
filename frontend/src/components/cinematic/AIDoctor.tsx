import { useEffect, useRef } from "react";
import "./AIDoctor.css";

/**
 * AIDoctor — a cinematic AI doctor/robot figure for the hero section.
 *
 * Design philosophy:
 * - Abstract, sophisticated representation of AI intelligence
 * - Geometric precision with subtle organic motion
 * - Premium materials: glass, chrome, subtle glow
 * - GPU-friendly CSS animations (transform/opacity only)
 * - Respects prefers-reduced-motion
 * - Scales responsively
 */
export function AIDoctor() {
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const reduceMotion = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    );
    if (reduceMotion.matches) return;

    const handleMove = (e: MouseEvent) => {
      const container = containerRef.current;
      if (!container) return;

      const rect = container.getBoundingClientRect();
      const centerX = rect.left + rect.width / 2;
      const centerY = rect.top + rect.height / 2;
      const deltaX = (e.clientX - centerX) / rect.width;
      const deltaY = (e.clientY - centerY) / rect.height;

      const core = container.querySelector(".ai-doctor__core") as HTMLElement | null;
      const halo = container.querySelector(".ai-doctor__halo") as HTMLElement | null;
      const accents = container.querySelectorAll(".ai-doctor__accent");

      if (core) {
        core.style.transform = `translate(${deltaX * 8}px, ${deltaY * 8}px)`;
      }
      if (halo) {
        halo.style.transform = `translate(${deltaX * 12}px, ${deltaY * 12}px) scale(1.02)`;
      }
      accents.forEach((el, i) => {
        const factor = (i + 1) * 4;
        (el as HTMLElement).style.transform = `translate(${deltaX * factor}px, ${deltaY * factor}px) rotate(${deltaX * 3}deg)`;
      });
    };

    const handleLeave = () => {
      const core = containerRef.current?.querySelector(".ai-doctor__core") as HTMLElement | null;
      const halo = containerRef.current?.querySelector(".ai-doctor__halo") as HTMLElement | null;
      const accents = containerRef.current?.querySelectorAll(".ai-doctor__accent");

      if (core) core.style.transform = "translate(0, 0)";
      if (halo) halo.style.transform = "translate(0, 0) scale(1)";
      accents?.forEach((el) => {
        (el as HTMLElement).style.transform = "translate(0, 0) rotate(0deg)";
      });
    };

    window.addEventListener("mousemove", handleMove);
    window.addEventListener("mouseleave", handleLeave);

    return () => {
      window.removeEventListener("mousemove", handleMove);
      window.removeEventListener("mouseleave", handleLeave);
    };
  }, []);

  return (
    <div
      ref={containerRef}
      className="ai-doctor"
      aria-hidden="true"
      role="img"
      aria-label="DEDAN Remote AI intelligence visualization"
    >
      <div className="ai-doctor__halo" />
      <div className="ai-doctor__figure">
        <div className="ai-doctor__core">
          <div className="ai-doctor__nucleus" />
          <div className="ai-doctor__iris" />
          <div className="ai-doctor__pupil" />
        </div>
        <div className="ai-doctor__orbits">
          <div className="ai-doctor__orbit ai-doctor__orbit--1">
            <div className="ai-doctor__orbit-node" />
            <div className="ai-doctor__orbit-node" />
            <div className="ai-doctor__orbit-node" />
            <div className="ai-doctor__orbit-node" />
          </div>
          <div className="ai-doctor__orbit ai-doctor__orbit--2">
            <div className="ai-doctor__orbit-node" />
            <div className="ai-doctor__orbit-node" />
            <div className="ai-doctor__orbit-node" />
            <div className="ai-doctor__orbit-node" />
            <div className="ai-doctor__orbit-node" />
            <div className="ai-doctor__orbit-node" />
          </div>
          <div className="ai-doctor__orbit ai-doctor__orbit--3">
            <div className="ai-doctor__orbit-node" />
            <div className="ai-doctor__orbit-node" />
            <div className="ai-doctor__orbit-node" />
            <div className="ai-doctor__orbit-node" />
            <div className="ai-doctor__orbit-node" />
            <div className="ai-doctor__orbit-node" />
            <div className="ai-doctor__orbit-node" />
            <div className="ai-doctor__orbit-node" />
          </div>
        </div>
        <div className="ai-doctor__accent ai-doctor__accent--1" />
        <div className="ai-doctor__accent ai-doctor__accent--2" />
        <div className="ai-doctor__accent ai-doctor__accent--3" />
        <div className="ai-doctor__accent ai-doctor__accent--4" />
        <div className="ai-doctor__accent ai-doctor__accent--5" />
        <div className="ai-doctor__accent ai-doctor__accent--6" />
        <div className="ai-doctor__accent ai-doctor__accent--7" />
        <div className="ai-doctor__accent ai-doctor__accent--8" />
        <svg className="ai-doctor__neural" viewBox="0 0 200 200" preserveAspectRatio="none">
          <defs>
            <linearGradient id="neural-gradient" x1="0%" y1="0%" x2="100%" y2="100%">
              {/* Bound to the brand tokens (not literals) so the neural web
                  re-tints automatically if the logo palette ever shifts. */}
              <stop offset="0%" stopColor="var(--brand-cyan)" stopOpacity="0.16" />
              <stop offset="50%" stopColor="var(--brand-violet)" stopOpacity="0.09" />
              <stop offset="100%" stopColor="var(--brand-mid)" stopOpacity="0.16" />
            </linearGradient>
          </defs>
          <g stroke="url(#neural-gradient)" strokeWidth="0.5" fill="none">
            <path d="M100,100 C80,80 60,60 40,40" />
            <path d="M100,100 C120,80 140,60 160,40" />
            <path d="M100,100 C80,120 60,140 40,160" />
            <path d="M100,100 C120,120 140,140 160,160" />
            <path d="M100,100 C70,95 40,90 20,85" />
            <path d="M100,100 C130,95 160,90 180,85" />
            <path d="M100,100 C70,105 40,110 20,115" />
            <path d="M100,100 C130,105 160,110 180,115" />
          </g>
        </svg>
        <div className="ai-doctor__scanline" />
        <div className="ai-doctor__status">
          <span className="ai-doctor__status-dot" />
          <span className="ai-doctor__status-text">SYSTEM ACTIVE</span>
        </div>
      </div>
      <div className="ai-doctor__reflection" />
    </div>
  );
}
