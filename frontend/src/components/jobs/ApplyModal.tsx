import { Modal } from "../common/Modal";
import type { JobDetail } from "../../types";
import "./ApplyModal.css";

interface ApplyModalProps {
  open: boolean;
  job: JobDetail;
  onCancel: () => void;
  /** Called when the user confirms leaving to the official source. */
  onConfirm: () => void;
}

/**
 * "You're leaving DEDAN Remote" confirmation.
 *
 * DEDAN Remote does not submit applications — every application continues
 * on the original, validated source URL.
 */
export function ApplyModal({
  open,
  job,
  onCancel,
  onConfirm,
}: ApplyModalProps) {
  const canApply = !!job.apply_url;

  return (
    <Modal
      open={open}
      onClose={onCancel}
      title="You're leaving DEDAN Remote"
      ariaLabel="Leave DEDAN Remote confirmation"
    >
      <div className="apply-modal">
        <p className="apply-modal__lead">
          You'll continue to the original opportunity source to submit your
          application. DEDAN Remote does not host applications.
        </p>

        <div className="apply-modal__target glass">
          <span className="apply-modal__label">Official source</span>
          <span className="apply-modal__host mono">
            {job.apply_host ?? "unavailable"}
          </span>
          <span className="apply-modal__src-note">
            {job.source_info.name} · discovered {job.freshness.label.toLowerCase()}
          </span>
        </div>

        {!canApply && (
          <p className="apply-modal__warning" role="alert">
            ⚠ This listing's URL could not be validated, so leaving is
            disabled. You can still open the original listing below.
          </p>
        )}

        <div className="apply-modal__actions">
          <button type="button" className="btn btn-secondary"
            onClick={onCancel}>
            Cancel
          </button>
          {canApply && (
            <a
              className="btn btn-primary"
              href={job.apply_url!}
              target="_blank"
              rel="noopener noreferrer nofollow"
              onClick={onConfirm}
            >
              Continue to application ↗
            </a>
          )}
        </div>

        {!canApply && job.url && (
          <a className="apply-modal__fallback"
            href={job.url} target="_blank" rel="noopener noreferrer nofollow">
            Open original listing anyway ↗
          </a>
        )}
      </div>
    </Modal>
  );
}
