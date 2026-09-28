/**
 * Hero Scroll Indicator - Visual cue encouraging users to scroll down
 * Shows that there's more content below the hero section
 * 
 * Part of the Hero Component Set (Set 04)
 */
export function HeroScrollIndicator() {
  return (
    <div className="hero-scroll-indicator">
      <div className="scroll-indicator-icon">
        {/* Down arrow or animation */}
      </div>
      <span className="scroll-indicator-text">
        Scroll to explore
      </span>
    </div>
  );
}
