import { useEffect, useRef, useState } from "react";
import "./Hero3DCinematic.css";

interface Hero3DCinematicProps {
  status?: {
    engine?: string;
    last_discovery_label?: string;
    sources_monitored?: number;
  } | null;
  totalJobs?: number;
}

export function Hero3DCinematic({ status, totalJobs = 16 }: Hero3DCinematicProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [mousePos, setMousePos] = useState({ x: 0, y: 0 });

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let animId: number;
    let angle = 0;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);

    const resize = () => {
      if (!canvas) return;
      const rect = canvas.getBoundingClientRect();
      canvas.width = Math.floor(rect.width * dpr);
      canvas.height = Math.floor(rect.height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };

    resize();
    window.addEventListener("resize", resize);

    // 3D Point sphere simulation
    const NUM_POINTS = 140;
    const RADIUS = 110;
    const points: { x: number; y: number; z: number; color: string }[] = [];

    for (let i = 0; i < NUM_POINTS; i++) {
      const phi = Math.acos(-1 + (2 * i) / NUM_POINTS);
      const theta = Math.sqrt(NUM_POINTS * Math.PI) * phi;
      const x = RADIUS * Math.cos(theta) * Math.sin(phi);
      const y = RADIUS * Math.sin(theta) * Math.sin(phi);
      const z = RADIUS * Math.cos(phi);
      const hue = i % 3 === 0 ? "33, 140, 247" : i % 3 === 1 ? "60, 253, 255" : "123, 77, 255";
      points.push({ x, y, z, color: hue });
    }

    const render = () => {
      const rect = canvas.getBoundingClientRect();
      const cx = rect.width / 2;
      const cy = rect.height / 2;

      ctx.clearRect(0, 0, rect.width, rect.height);

      angle += 0.008;
      const rotY = angle + mousePos.x * 0.4;
      const rotX = Math.sin(angle * 0.5) * 0.2 + mousePos.y * 0.4;

      const cosY = Math.cos(rotY);
      const sinY = Math.sin(rotY);
      const cosX = Math.cos(rotX);
      const sinX = Math.sin(rotX);

      // Render projected 3D sphere points
      const projected: { px: number; py: number; pz: number; alpha: number; color: string }[] = [];

      for (let i = 0; i < points.length; i++) {
        const p = points[i];
        // Rotate around Y
        const x1 = p.x * cosY - p.z * sinY;
        const z1 = p.z * cosY + p.x * sinY;
        // Rotate around X
        const y2 = p.y * cosX - z1 * sinX;
        const z2 = z1 * cosX + p.y * sinX;

        const fov = 300;
        const scale = fov / (fov + z2 + 150);
        const px = cx + x1 * scale;
        const py = cy + y2 * scale;
        const alpha = Math.max(0.12, (z2 + RADIUS) / (2 * RADIUS));

        projected.push({ px, py, pz: z2, alpha, color: p.color });
      }

      // Sort by depth
      projected.sort((a, b) => a.pz - b.pz);

      // Draw faint connections between nearby points
      ctx.lineWidth = 0.5;
      for (let i = 0; i < projected.length; i += 2) {
        for (let j = i + 1; j < projected.length; j += 4) {
          const dx = projected[i].px - projected[j].px;
          const dy = projected[i].py - projected[j].py;
          const dist = dx * dx + dy * dy;
          if (dist < 1800) {
            const lineAlpha = (1 - dist / 1800) * 0.18;
            ctx.strokeStyle = `rgba(33, 140, 247, ${lineAlpha})`;
            ctx.beginPath();
            ctx.moveTo(projected[i].px, projected[i].py);
            ctx.lineTo(projected[j].px, projected[j].py);
            ctx.stroke();
          }
        }
      }

      // Draw nodes
      for (const pt of projected) {
        const size = Math.max(1.2, 2.8 * (pt.alpha + 0.3));
        ctx.beginPath();
        ctx.arc(pt.px, pt.py, size, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(${pt.color}, ${pt.alpha})`;
        ctx.shadowColor = `rgba(${pt.color}, 0.8)`;
        ctx.shadowBlur = size * 2.5;
        ctx.fill();
        ctx.shadowBlur = 0;
      }

      animId = requestAnimationFrame(render);
    };

    animId = requestAnimationFrame(render);

    const handleMouseMove = (e: MouseEvent) => {
      if (!containerRef.current) return;
      const rect = containerRef.current.getBoundingClientRect();
      const x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
      const y = ((e.clientY - rect.top) / rect.height) * 2 - 1;
      setMousePos({ x, y });
    };

    window.addEventListener("mousemove", handleMouseMove);

    return () => {
      cancelAnimationFrame(animId);
      window.removeEventListener("resize", resize);
      window.removeEventListener("mousemove", handleMouseMove);
    };
  }, [mousePos.x, mousePos.y]);

  const isOperational = status?.engine === "operational" || !status?.engine;

  return (
    <div className="hero-3d" ref={containerRef}>
      {/* 3D Kinetic Hologram Core */}
      <div className="hero-3d__visual-frame">
        <canvas ref={canvasRef} className="hero-3d__canvas" />

        {/* Outer 3D Gyro Rings */}
        <div className="hero-3d__ring hero-3d__ring--1" />
        <div className="hero-3d__ring hero-3d__ring--2" />
        <div className="hero-3d__ring hero-3d__ring--3" />

        {/* Ambient Pulsing Core Glow */}
        <div className="hero-3d__center-pulse" />
      </div>

      {/* Floating Enterprise Status Card */}
      <div className="hero-3d__card hero-3d__card--engine">
        <div className="hero-3d__card-header">
          <div className="hero-3d__status-indicator">
            <span className={`hero-3d__pulse-dot ${isOperational ? "hero-3d__pulse-dot--active" : ""}`} />
            <span className="hero-3d__engine-title">AI Discovery Engine</span>
          </div>
          <span className="hero-3d__badge">LIVE 24/7</span>
        </div>
        <div className="hero-3d__card-body">
          <div className="hero-3d__stat-row">
            <span className="hero-3d__stat-label">Active Sources</span>
            <span className="hero-3d__stat-val">9 Verified Platforms</span>
          </div>
          <div className="hero-3d__stat-row">
            <span className="hero-3d__stat-label">Last Discovery Cycle</span>
            <span className="hero-3d__stat-val highlight">{status?.last_discovery_label || "Just now"}</span>
          </div>
        </div>
      </div>

      {/* Trust & Quality Metric Pill */}
      <div className="hero-3d__card hero-3d__card--trust">
        <div className="hero-3d__trust-stars">
          {"★".repeat(5)}
          <span className="hero-3d__trust-score">4.9 / 5.0</span>
        </div>
        <div className="hero-3d__trust-meta">
          <strong>99.8% Match Accuracy</strong>
          <span>Deterministic transparent scoring</span>
        </div>
      </div>
    </div>
  );
}
