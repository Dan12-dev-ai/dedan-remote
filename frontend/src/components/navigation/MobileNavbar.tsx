/**
 * MobileNavbar — the navigation for < 768px.
 *
 * A bottom-mounted bar rather than a top one: on a phone the top of the screen
 * is the hardest place to reach one-handed, and the product's primary action
 * (open the menu, search) should be inside thumb range.
 *
 * The drawer rises from above the bar, so the control that opened it stays
 * visible and does not shift when the drawer appears.
 */
import { useEffect, useState } from "react";
import { Brand } from "../brand";
import { NavAction } from "./NavAction";
import { SearchTrigger } from "./SearchTrigger";
import { MobileMenuContent } from "./MobileMenuContent";
import { CloseIcon, MenuIcon } from "./icons";
import "./Navigation.css";

export function MobileNavbar({
  onAuthClick,
  onOpenPalette,
}: {
  onAuthClick: () => void;
  onOpenPalette: () => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);

  // Escape closes the drawer, and the drawer is a modal dialog so it must trap
  // focus — `MobileMenuContent` owns its own focus handling and receives the
  // close callback rather than reaching into this component's state.
  useEffect(() => {
    if (!menuOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenuOpen(false);
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [menuOpen]);

  return (
    <header className="mobile-navbar">
      <div className="mobile-navbar__inner">
        <Brand variant="composite" size="xs" to="/" className="mobile-navbar__brand" />

        <div className="mobile-navbar__actions">
          <SearchTrigger onOpenPalette={onOpenPalette} />
          <NavAction
            label={menuOpen ? "Close menu" : "Open menu"}
            hasPopup="dialog"
            expanded={menuOpen}
            controls="mobile-menu-drawer"
            onClick={() => setMenuOpen((v) => !v)}
          >
            {menuOpen ? <CloseIcon /> : <MenuIcon />}
          </NavAction>
        </div>
      </div>

      {menuOpen && (
        <div
          id="mobile-menu-drawer"
          className="mobile-navbar__drawer"
          role="dialog"
          aria-modal="true"
          aria-label="Menu"
        >
          <MobileMenuContent
            onCloseMenu={() => setMenuOpen(false)}
            onAuthClick={() => {
              setMenuOpen(false);
              onAuthClick();
            }}
          />
        </div>
      )}
    </header>
  );
}
