import { useEffect, useRef, useState } from "react";

import { ClipboardIcon } from "./icons";

const COPIED_RESET_MS = 2000;

/** Copy text with the Clipboard API, falling back to a hidden textarea. */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Permission denied or an insecure context: try the legacy path below.
  }
  try {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    area.style.pointerEvents = "none";
    document.body.appendChild(area);
    area.select();
    const copied = document.execCommand("copy");
    document.body.removeChild(area);
    return copied;
  } catch {
    return false;
  }
}

export function shortMagnet(magnet: string | null | undefined): string {
  if (!magnet) {
    return "magnet missing";
  }
  const match = /[?&]xt=urn:btih:([a-zA-Z0-9]+)/i.exec(magnet);
  const hash = match?.[1] ?? "";
  return hash ? `magnet:?xt=urn:btih:${hash.slice(0, 4)}…` : "magnet:?xt=urn:btih:…";
}

export function CopyMagnet({
  magnet,
  compact = false,
  iconOnly = false,
}: {
  magnet: string | null | undefined;
  compact?: boolean;
  iconOnly?: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (timer.current) {
        clearTimeout(timer.current);
      }
    },
    [],
  );

  async function handleCopy() {
    if (!magnet) {
      return;
    }
    const ok = await copyText(magnet);
    if (!ok) {
      return;
    }
    setCopied(true);
    if (timer.current) {
      clearTimeout(timer.current);
    }
    timer.current = setTimeout(() => setCopied(false), COPIED_RESET_MS);
  }

  return (
    <div className={iconOnly ? "flex items-center gap-1" : "flex flex-wrap items-center gap-1.5"}>
      <button
        type="button"
        aria-label="Copy magnet link"
        title={magnet ? "Copy the full magnet link" : "No magnet is stored for this release"}
        disabled={!magnet}
        onClick={() => void handleCopy()}
        className={[
          "rounded-control border border-border-strong text-small font-semibold text-fg hover:bg-hover disabled:cursor-not-allowed disabled:opacity-40",
          iconOnly
            ? "flex h-9 w-9 items-center justify-center"
            : compact
              ? "min-h-control-sm px-2"
              : "min-h-control-sm px-panel-compact",
        ].join(" ")}
      >
        {iconOnly ? <ClipboardIcon /> : "Copy"}
      </button>
      {iconOnly ? null : (
        <span className="text-micro text-fg-comment" aria-hidden="true">
          {shortMagnet(magnet)}
        </span>
      )}
      {copied ? (
        <span role="status" className="text-micro text-success">
          Copied
        </span>
      ) : null}
      {!iconOnly && !magnet ? (
        <span className="text-micro text-fg-comment">no magnet</span>
      ) : null}
    </div>
  );
}
