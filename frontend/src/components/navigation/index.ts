/**
 * Navigation component set (Set 03).
 *
 * `Navbar` is the composition entry point; everything else is a piece of it.
 *
 * Removed while consolidating this set:
 *   Logo.tsx / Brand.tsx           superseded by components/brand
 *   CommandPaletteTrigger.tsx      merged into SearchTrigger (two buttons, one action)
 *   MobileMenuTrigger.tsx          the mobile bar owns its own trigger
 *   SecondaryNavigation.tsx        a <nav> landmark wrapping a single logo link
 *   NavigationLinks.tsx            empty stub
 */
export { Navbar } from "./Navbar";
export { DesktopNavbar } from "./DesktopNavbar";
export { MobileNavbar } from "./MobileNavbar";
export { MobileMenuContent } from "./MobileMenuContent";
export { Breadcrumbs } from "./Breadcrumbs";
export type { BreadcrumbItem } from "./Breadcrumbs";
export { NavAction } from "./NavAction";
export type { NavActionProps } from "./NavAction";
export { SearchTrigger } from "./SearchTrigger";
export { NotificationButton } from "./NotificationButton";
export { SignInButton } from "./SignInButton";
export { UserMenu } from "./UserMenu";

