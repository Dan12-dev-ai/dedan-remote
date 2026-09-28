/**
 * Navbar — renders the full navigation set.
 *
 * Both bars are mounted and the switch between them is pure CSS. The previous
 * implementation picked one with a JavaScript media query, which meant the wrong
 * bar could paint before the query resolved and the two could never be compared
 * side by side in testing.
 */
import { DesktopNavbar } from "./DesktopNavbar";
import { MobileNavbar } from "./MobileNavbar";

export function Navbar({
  onAuthClick,
  onOpenPalette,
}: {
  onAuthClick: () => void;
  onOpenPalette: () => void;
}) {
  return (
    <>
      <DesktopNavbar onAuthClick={onAuthClick} onOpenPalette={onOpenPalette} />
      <MobileNavbar onAuthClick={onAuthClick} onOpenPalette={onOpenPalette} />
    </>
  );
}

