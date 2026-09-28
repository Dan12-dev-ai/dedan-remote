/**
 * Hero Title - Main title of the hero section
 * Can include gradients and rich text formatting
 * 
 * Part of the Hero Component Set (Set 04)
 */
import { PropsWithChildren } from "react";

export function HeroTitle({ children }: PropsWithChildren<{}> ) {
  return (
    <h1 className="hero-title">
      {children}
    </h1>
  );
}
