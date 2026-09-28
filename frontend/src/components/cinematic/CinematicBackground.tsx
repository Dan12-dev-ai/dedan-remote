import { useEffect, useRef } from "react";
import "./CinematicBackground.css";

/**
 * CinematicBackground — the ambient environment for DEDAN Remote.
 *
 * Performance design:
 * - Layered CSS gradients animate on the compositor (transform/opacity only)
 * - Optional particle canvas pauses when off-screen, hidden, or when the
 *   user prefers reduced motion; particle count adapts to viewport size
 * - Never interferes with content readability (low opacity, aria-hidden)
 */
export function CinematicBackground() {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const reduceMotion = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    );
    if (reduceMotion.matches) {
      canvas.style.display = "none";
      return;
    }

    const ctx = canvas.getContext("2d", { alpha: true });
    if (!ctx) return;

    let raf = 0;
    let running = true;
    let width = 0;
    let height = 0;

    type Particle = {
      x: number; y: number; vx: number; vy: number;
      r: number; a: number;
    };
    let particles: Particle[] = [];

    const density = () =>
      Math.min(56, Math.max(22, Math.floor((width * height) / 34_000)));

    const seed = () => {
      particles = Array.from({ length: density() }, () => ({
        x: Math.random() * width,
        y: Math.random() * height,
        vx: (Math.random() - 0.5) * 0.14,
        vy: -0.05 - Math.random() * 0.12,
        r: 0.6 + Math.random() * 1.3,
        a: 0.12 + Math.random() * 0.3,
      }));
    };

    const resize = () => {
      width = canvas.clientWidth;
      height = canvas.clientHeight;
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.floor(width * dpr);
      canvas.height = Math.floor(height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      seed();
    };

    const frame = () => {
      if (!running) return;
      ctx.clearRect(0, 0, width, height);

      // Draw particles + faint connecting "data lines" near neighbors.
      for (const p of particles) {
        p.x += p.vx;
        p.y += p.vy;
        if (p.y < -4) { p.y = height + 4; p.x = Math.random() * width; }
        if (p.x < -4) p.x = width + 4;
        if (p.x > width + 4) p.x = -4;

        ctx.beginPath();
        ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(140, 180, 255, ${p.a})`;
        ctx.fill();
      }

      for (let i = 0; i < particles.length; i++) {
        for (let j = i + 1; j < particles.length; j++) {
          const a = particles[i];
          const b = particles[j];
          const dx = a.x - b.x;
          const dy = a.y - b.y;
          const d2 = dx * dx + dy * dy;
          if (d2 < 14_000) {
            const alpha = 0.05 * (1 - d2 / 14_000);
            ctx.strokeStyle = `rgba(120, 160, 255, ${alpha})`;
            ctx.lineWidth = 0.6;
            ctx.beginPath();
            ctx.moveTo(a.x, a.y);
            ctx.lineTo(b.x, b.y);
            ctx.stroke();
          }
        }
      }
      raf = requestAnimationFrame(frame);
    };

    const onVisibility = () => {
      if (document.hidden) {
        running = false;
        cancelAnimationFrame(raf);
      } else if (!running) {
        running = true;
        raf = requestAnimationFrame(frame);
      }
    };

    const onMotionChange = (e: MediaQueryListEvent) => {
      if (e.matches) {
        running = false;
        cancelAnimationFrame(raf);
        ctx.clearRect(0, 0, width, height);
        canvas.style.display = "none";
      } else {
        canvas.style.display = "";
        running = true;
        raf = requestAnimationFrame(frame);
      }
    };

    resize();
    raf = requestAnimationFrame(frame);
    window.addEventListener("resize", resize);
    document.addEventListener("visibilitychange", onVisibility);
    reduceMotion.addEventListener("change", onMotionChange);

    return () => {
      running = false;
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", resize);
      document.removeEventListener("visibilitychange", onVisibility);
      reduceMotion.removeEventListener("change", onMotionChange);
    };
  }, []);

  return (
    <div className="cinematic" aria-hidden="true">
      <div className="cinematic__gradient" />
      <div className="cinematic__aurora cinematic__aurora--a" />
      <div className="cinematic__aurora cinematic__aurora--b" />
      <div className="cinematic__grid" />
      <div className="cinematic__vignette" />
      <canvas ref={canvasRef} className="cinematic__particles" />
    </div>
  );
}
