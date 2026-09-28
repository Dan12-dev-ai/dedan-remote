/**
 * Hero Actions - Container for primary call-to-action buttons in the hero
 * 
 * Part of the Hero Component Set (Set 04)
 */
import { PropsWithChildren } from "react";

export function HeroActions({ children }: PropsWithChildren<{}> ) {
  return (
    <div className="hero-actions">
      {children}
    </div>
  );
}
