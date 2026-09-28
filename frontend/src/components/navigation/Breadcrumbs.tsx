/**
 * Breadcrumbs — hierarchical wayfinding.
 *
 * Renders through `Link` so navigation stays client-side (a plain anchor would
 * trigger a full document reload and drop SPA state). The trailing crumb is
 * announced as the current page and is deliberately not a link — offering a
 * link to where you already are is a dead end.
 */
import { Fragment } from "react";
import { Link } from "react-router-dom";

export interface BreadcrumbItem {
  label: string;
  to?: string;
}

export function Breadcrumbs({ items }: { items?: BreadcrumbItem[] }) {
  const crumbs: BreadcrumbItem[] =
    items && items.length > 0 ? items : [{ label: "Home", to: "/" }];

  return (
    <nav className="breadcrumbs" aria-label="Breadcrumb">
      <ol className="breadcrumbs__list">
        {crumbs.map((item, index) => {
          const isLast = index === crumbs.length - 1;
          return (
            <Fragment key={`${item.to ?? "current"}-${index}`}>
              <li className="breadcrumbs__item">
                {item.to && !isLast ? (
                  <Link to={item.to} className="breadcrumbs__link">
                    {item.label}
                  </Link>
                ) : (
                  <span aria-current="page">{item.label}</span>
                )}
              </li>
              {!isLast && (
                <li className="breadcrumbs__separator" aria-hidden="true">
                  /
                </li>
              )}
            </Fragment>
          );
        })}
      </ol>
    </nav>
  );
}
