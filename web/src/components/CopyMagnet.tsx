import { useEffect, useRef, useState } from "react";

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
}: {
  magnet: string | null | undefined;
  compact?: boolean;
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
    <div className="flex flex-wrap items-center gap-1.5">
      <button
        type="button"
        aria-label="Copy magnet link"
        title={magnet ? "Copy the full magnet link" : "No magnet is stored for this release"}
        disabled={!magnet}
        onClick={() => void handleCopy()}
        className={[
          "rounded-control border border-border-strong text-[12px] font-semibold text-fg hover:bg-hover disabled:cursor-not-allowed disabled:opacity-40",
          compact ? "min-h-7 px-2" : "min-h-8 px-2.5",
        ].join(" ")}
      >
        Copy
      </button>
      <span className="text-micro text-fg-comment" aria-hidden="true">
        {shortMagnet(magnet)}
      </span>
      {copied ? (
        <span role="status" className="text-micro text-success">
          Copied
        </span>
      ) : null}
      {!magnet ? <span className="text-micro text-fg-comment">no magnet</span> : null}
    </div>
  );
}
