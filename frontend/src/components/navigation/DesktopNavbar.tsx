/**
 * DesktopNavbar — the primary navigation for >= 768px.
 *
 * Composition follows the three-zone model the rest of the product uses:
 *   left   context       the brand, which always returns you home
 *   centre the task      primary destinations
 *   right  intelligence  search, notifications, the account
 *
 * Scroll behaviour: `scrolled` gains the panel background, `compact` shrinks the
 * bar. Both are applied from a single requestAnimationFrame-guarded listener —
 * the previous implementation called `setState` on every scroll event, which
 * re-rendered the whole navigation (and its feed) dozens of times per second.
 */
import { useEffect, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { Brand } from "../brand";
import { useAuth } from "../../stores/AuthContext";
import { SearchTrigger } from "./SearchTrigger";
import { NotificationButton } from "./NotificationButton";
import { UserMenu } from "./UserMenu";
import { SignInButton } from "./SignInButton";
import "./Navigation.css";

interface NavItem {
  to: string;
  label: string;
  /** Only shown once the visitor has an account. */
  auth?: boolean;
}

const LINKS: NavItem[] = [
  { to: "/opportunities", label: "Explore" },
  { to: "/categories", label: "Categories" },
  { to: "/saved", label: "Saved", auth: true },
  { to: "/applications", label: "Applications", auth: true },
];

export function DesktopNavbar({
  onAuthClick,
  onOpenPalette,
}: {
  onAuthClick: () => void;
  onOpenPalette: () => void;
}) {
  const { user, signOut } = useAuth();
  const { pathname } = useLocation();
  const [scrolled, setScrolled] = useState(false);
  const [compact, setCompact] = useState(false);

  useEffect(() => {
    let frame = 0;

    const read = () => {
      frame = 0;
      const y = window.scrollY;
      // Functional updates bail out on unchanged values, so scrolling within a
      // band costs no re-render at all.
      setScrolled((prev) => (prev === y > 10 ? prev : y > 10));
      setCompact((prev) => (prev === y > 90 ? prev : y > 90));
    };

    const onScroll = () => {
      if (frame) return;
      frame = window.requestAnimationFrame(read);
    };

    window.addEventListener("scroll", onScroll, { passive: true });
    read();
    return () => {
      window.removeEventListener("scroll", onScroll);
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, []);

  const visibleLinks = LINKS.filter((link) => !link.auth || user);

  return (
    <header
      className={[
        "desktop-navbar",
        scrolled ? "is-scrolled" : "",
        compact ? "is-compact" : "",
        // The landing hero is a full-bleed cinematic band, so the bar starts
        // transparent and gains its surface only once content scrolls under it.
        pathname === "/" ? "is-over-hero" : "",
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <div className="container container--wide desktop-navbar__inner">
        <Brand variant="composite" size="sm" to="/" />

        <nav className="desktop-navbar__primary" aria-label="Primary">
          {visibleLinks.map((link) => (
            <NavLink
              key={link.to}
              to={link.to}
              className={({ isActive }) =>
                `desktop-navbar__link${isActive ? " is-active" : ""}`
              }
            >
              {link.label}
            </NavLink>
          ))}
        </nav>

        <div className="desktop-navbar__actions">
          <SearchTrigger onOpenPalette={onOpenPalette} />
          <NotificationButton />
          {user ? (
            <UserMenu
              label="Account menu"
              trigger={
                <span className="navbar-avatar" aria-hidden="true">
                  {(user.display_name || user.email).charAt(0).toUpperCase()}
                </span>
              }
            >
              <NavLink to="/profile" className="user-menu__item">
                Profile
              </NavLink>
              <NavLink to="/recommendations" className="user-menu__item">
                Recommendations
              </NavLink>
              <NavLink to="/assistant" className="user-menu__item">
                Assistant
              </NavLink>
              <div className="user-menu__divider" />
              <SignInButton variant="ghost" onClick={signOut}>
                Sign out
              </SignInButton>
            </UserMenu>
          ) : (
            <SignInButton onClick={onAuthClick}>Sign in</SignInButton>
          )}
        </div>
      </div>
    </header>
  );
}
