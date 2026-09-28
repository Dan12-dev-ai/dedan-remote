/**
 * Hero Shell — the landing hero.
 *
 * Two-column composition:
 *   left   message      eyebrow, headline, copy, search, social proof
 *   right  intelligence the live workbench dashboard (real API data)
 *
 * Both columns are grid children of `.hero-shell-content`, aligned on the
 * shared baseline, so nothing overlaps and neither column is starved.
 */
import "./HeroShell.css";
import { HeroEyebrow } from "./HeroEyebrow";
import { HeroTitle } from "./HeroTitle";
import { HeroSubtitle } from "./HeroSubtitle";
import { HeroSearch } from "./HeroSearch";
import { HeroVisual } from "./HeroVisual";

export function HeroShell({
  children,
  className = "",
}: {
  children?: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={`hero-shell ${className}`}>
      <div className="hero-shell-content">
        {/* Hero Content Zone — left column */}
        <div className="hero-content">
          <HeroEyebrow />
          <HeroTitle>
            Discover work
            <br />
            <span className="hero-title-gradient">that finds you</span>
          </HeroTitle>
          <HeroSubtitle>
            AI-powered discovery of remote work, AI training, data, software
            and digital opportunities — sourced automatically from platforms
            worldwide.
          </HeroSubtitle>

          <HeroSearch placeholder="Search 10M+ global opportunities…" />

          {/* Business-level customer rating & social proof bar */}
          <div className="hero-social-proof">
            <div className="hero-rating-stars">
              <span className="stars">★★★★★</span>
              <span className="rating-num">4.9/5</span>
            </div>
            <div className="hero-rating-divider" />
            <div className="hero-rating-text">
              Trusted by <strong>10,000+</strong> remote professionals &amp;
              AI contributors worldwide
            </div>
          </div>

          {children}
        </div>

        {/* Hero Visual Zone — right column: live workbench dashboard */}
        <div className="hero-visual">
          <HeroVisual />
        </div>
      </div>
    </section>
  );
}
