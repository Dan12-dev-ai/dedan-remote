/**
 * Hero Subtitle - Supporting text below the hero title
 * Provides additional context about the platform
 * 
 * Part of the Hero Component Set (Set 04)
 */
import { PropsWithChildren } from "react";

export function HeroSubtitle({ children }: PropsWithChildren<{}> ) {
  return (
    <p className="hero-subtitle">
      {children}
    </p>
  );
}
