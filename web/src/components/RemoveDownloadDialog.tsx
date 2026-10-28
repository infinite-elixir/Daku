import { useEffect, useId, useRef, useState } from "react";

export interface RemoveDownloadDialogProps {
  name: string;
  showDeleteFiles: boolean;
  onCancel: () => void;
  onConfirm: (deleteFiles: boolean) => void;
}

export function RemoveDownloadDialog({
  name,
  showDeleteFiles,
  onCancel,
  onConfirm,
}: RemoveDownloadDialogProps) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const bodyId = useId();
  const [deleteFiles, setDeleteFiles] = useState(false);

  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    cancelRef.current?.focus();

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        onCancel();
        return;
      }
      if (event.key !== "Tab" || !dialogRef.current) {
        return;
      }
      const nodes = Array.from(
        dialogRef.current.querySelectorAll<HTMLElement>(
          "button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex='-1'])",
        ),
      ).filter((node) => node.offsetParent !== null);
      if (nodes.length === 0) {
        return;
      }
      const first = nodes[0];
      const last = nodes[nodes.length - 1];
      if (!first || !last) {
        return;
      }
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      previous?.focus();
    };
  }, [onCancel]);

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-[rgba(12,14,20,0.7)] p-0 sm:items-center sm:p-4">
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={bodyId}
        className="max-h-[90vh] w-full max-w-[440px] overflow-y-auto rounded-t-panel border border-border-strong bg-panel px-5 py-5 text-fg shadow-xl sm:rounded-panel"
      >
        <h2 id={titleId} className="text-h2 font-semibold tracking-tight">
          Remove download?
        </h2>
        <p id={bodyId} className="mt-2 break-words text-small text-fg-muted">
          This removes the queue entry. Files already in your library stay where they are.
        </p>
        <p className="mt-2 break-all text-small text-fg-muted">{name}</p>

        {showDeleteFiles ? (
          <label className="mt-4 flex items-start gap-2 rounded-control border border-border-subtle bg-canvas px-3 py-3 text-small">
            <input
              type="checkbox"
              checked={deleteFiles}
              onChange={(event) => setDeleteFiles(event.target.checked)}
              className="mt-1 h-4 w-4 accent-danger"
            />
            <span>
              <span className="font-medium text-fg">Also delete the downloaded files</span>
              <span className="mt-0.5 block text-small text-fg-muted">
                This deletes qBittorrent&apos;s copy from the download folder, not files already
                organised into your library.
              </span>
            </span>
          </label>
        ) : null}

        <div className="mt-5 flex flex-wrap justify-end gap-2">
          <button
            ref={cancelRef}
            type="button"
            onClick={onCancel}
            className="min-h-9 rounded-control border border-border-subtle px-4 text-small font-semibold text-fg hover:bg-hover"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => onConfirm(deleteFiles)}
            className="min-h-9 rounded-control border border-danger/50 bg-danger/15 px-4 text-small font-semibold text-danger hover:bg-danger/25"
          >
            Remove
          </button>
        </div>
      </div>
    </div>
  );
}
