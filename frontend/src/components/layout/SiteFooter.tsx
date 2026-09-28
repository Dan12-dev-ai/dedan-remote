/**
 * SiteFooter — the closing band.
 *
 * Uses the `composite` brand variant rather than the raster lockup: at footer
 * scale a raster lockup would render its REMOTE sub-word at about 1.6px, whereas
 * the composite variant draws the wordmark as live text and stays crisp. The
 * tagline is therefore real text here too, which also makes it selectable,
 * translatable and available to assistive technology.
 */
import { Link } from "react-router-dom";
import { Brand } from "../brand";
import "./SiteFooter.css";

interface FooterColumn {
  heading: string;
  links: { to: string; label: string }[];
}

const COLUMNS: FooterColumn[] = [
  {
    heading: "Discover",
    links: [
      { to: "/opportunities", label: "Explore opportunities" },
      { to: "/categories", label: "Categories" },
      { to: "/recommendations", label: "Recommendations" },
      { to: "/sources", label: "Monitored sources" },
    ],
  },
  {
    heading: "Your work",
    links: [
      { to: "/saved", label: "Saved" },
      { to: "/applications", label: "Applications" },
      { to: "/assistant", label: "Assistant" },
      { to: "/profile", label: "Preferences" },
    ],
  },
  {
    heading: "Product",
    links: [
      { to: "/about", label: "About DEDAN Remote" },
      { to: "/opportunities", label: "Search" },
    ],
  },
];

export function SiteFooter() {
  return (
    <footer className="site-footer">
      <div className="container container--wide site-footer__inner">
        <div className="site-footer__brand">
          <Brand variant="composite" size="md" to="/" />
          <p className="site-footer__tagline">
            Discover <span aria-hidden="true">·</span> Apply{" "}
            <span aria-hidden="true">·</span> Build your future
          </p>
        </div>

        <nav className="site-footer__nav" aria-label="Footer">
          {COLUMNS.map((column) => (
            <div className="site-footer__column" key={column.heading}>
              {/* Column headings are labels for link groups, not document
                  headings — using <h2> here would add three spurious entries to
                  the page outline. */}
              <p className="micro site-footer__heading">{column.heading}</p>
              <ul className="site-footer__list">
                {column.links.map((link) => (
                  <li key={`${column.heading}-${link.to}-${link.label}`}>
                    <Link to={link.to} className="site-footer__link">
                      {link.label}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </nav>
      </div>

      <div className="container container--wide site-footer__legal">
        <p className="tertiary">
          © {new Date().getFullYear()} DEDAN Remote. Opportunities are discovered
          from third-party platforms and linked to their original source; no
          endorsement is implied.
        </p>
      </div>
    </footer>
  );
}
