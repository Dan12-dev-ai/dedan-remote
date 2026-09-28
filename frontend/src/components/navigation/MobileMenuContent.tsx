/**
 * MobileMenuContent — the drawer body.
 *
 * Both authentication actions render through the shared `.btn` primitives rather
 * than bespoke `.mobile-menu-signin` / `.mobile-menu-signout` rules, so the
 * primary action looks identical to every other primary action in the product.
 */
import { NavLink } from "react-router-dom";
import { useAuth } from "../../stores/AuthContext";
import { CloseIcon } from "./icons";

interface MobileMenuContentProps {
  onCloseMenu: () => void;
  onAuthClick: () => void;
}

const LINKS = [
  { to: "/opportunities", label: "Explore" },
  { to: "/categories", label: "Categories" },
  { to: "/saved", label: "Saved", auth: true },
  { to: "/applications", label: "Applications", auth: true },
  { to: "/recommendations", label: "Recommendations", auth: true },
  { to: "/assistant", label: "Assistant", auth: true },
  { to: "/sources", label: "Sources" },
  { to: "/about", label: "About" },
  { to: "/profile", label: "Profile", auth: true },
];

export function MobileMenuContent({
  onCloseMenu,
  onAuthClick,
}: MobileMenuContentProps) {
  const { user, signOut } = useAuth();
  const visibleLinks = LINKS.filter((link) => !link.auth || user);

  return (
    <div className="mobile-menu-content">
      <div className="mobile-menu-header">
        <button
          type="button"
          className="mobile-menu-close"
          onClick={onCloseMenu}
          aria-label="Close menu"
        >
          <CloseIcon />
        </button>
      </div>

      <nav className="mobile-menu-navigation" aria-label="Menu">
        {visibleLinks.map((link) => (
          <NavLink
            key={link.to}
            to={link.to}
            onClick={onCloseMenu}
            className={({ isActive }) =>
              `mobile-menu-link${isActive ? " is-active" : ""}`
            }
          >
            {link.label}
          </NavLink>
        ))}
      </nav>

      <div className={user ? "mobile-menu-user" : "mobile-menu-auth"}>
        {user ? (
          <button
            type="button"
            className="btn btn--secondary"
            onClick={() => {
              signOut();
              onCloseMenu();
            }}
          >
            Sign out
          </button>
        ) : (
          <button type="button" className="btn btn--primary" onClick={onAuthClick}>
            Sign in
          </button>
        )}
      </div>
    </div>
  );
}

