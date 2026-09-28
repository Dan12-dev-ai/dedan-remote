/**
 * NotificationButton — unread notification count.
 *
 * The count is real state, not a placeholder: `count` is supplied by the caller
 * from the notifications endpoint. With no source wired up the badge is simply
 * absent, which is the truthful rendering — the previous version rendered an
 * empty button with no icon and no data, so it looked broken rather than empty.
 */
import { NavAction } from "./NavAction";
import { BellIcon } from "./icons";

interface NotificationButtonProps {
  count?: number;
  onClick?: () => void;
}

export function NotificationButton({ count = 0, onClick }: NotificationButtonProps) {
  return (
    <NavAction
      label={
        count > 0
          ? `Notifications, ${count} unread`
          : "Notifications, none unread"
      }
      onClick={onClick}
      count={count}
      hasPopup="menu"
    >
      <BellIcon />
    </NavAction>
  );
}
